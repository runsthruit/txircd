from twisted.internet import reactor
from twisted.internet.defer import Deferred
from twisted.internet.task import LoopingCall
from twisted.words.protocols import irc
from txircd import version
from txircd.utils import ModeType, now, splitMessage
from copy import copy
from socket import gethostbyaddr, herror

irc.ERR_ALREADYREGISTERED = "462"

class IRCUser(irc.IRC):
    def __init__(self, ircd, ip, uuid = None, host = None):
        self.ircd = ircd
        self.uuid = ircd.createUUID() if uuid is None else uuid
        self.nick = None
        self.ident = None
        if host is None:
            try:
                host = gethostbyaddr(ip)[0]
            except herror:
                host = ip
        self.host = host
        self.realhost = host
        self.ip = ip
        self.gecos = None
        self.metadata = {
            "server": {},
            "user": {},
            "client": {},
            "ext": {},
            "private": {}
        }
        self.cache = {}
        self.channels = []
        self.modes = {}
        self.connectedSince = now()
        self.nickSince = now()
        self.idleSince = now()
        self._registerHolds = set(("NICK", "USER"))
        self.disconnectedDeferred = Deferred()
        self.ircd.users[self.uuid] = self
        self.localOnly = False
        self._pinger = LoopingCall(self._ping)
        self._registrationTimeoutTimer = reactor.callLater(self.ircd.config.getWithDefault("user_registration_timeout", 10), self._timeoutRegistration)
    
    def connectionMade(self):
        if "user_connect" in self.ircd.actions:
            for action in self.ircd.actions["user_connect"]:
                if not action[0](self):
                    self.transport.loseConnection()
                    return
    
    def dataReceived(self, data):
        data = data.replace("\r", "").replace("\n", "\r\n").replace("\0", "")
        if "user_recvdata" in self.ircd.actions:
            for action in self.ircd.actions["user_recvdata"]:
                action[0](self, data)
        irc.IRC.dataReceived(self, data)
    
    def sendLine(self, line):
        if "user_senddata" in self.ircd.actions:
            for action in self.ircd.actions["user_senddata"]:
                action[0](self, line)
        irc.IRC.sendLine(self, line)
    
    def sendMessage(self, command, *args, **kw):
        if "prefix" not in kw:
            kw["prefix"] = self.ircd.name
        if kw["prefix"] is None:
            del kw["prefix"]
        to = self.nick if self.nick else "*"
        if "to" in kw:
            to = kw["to"]
            del kw["to"]
        if to:
            irc.IRC.sendMessage(self, command, to, *args, **kw)
        else:
            irc.IRC.sendMessage(self, command, *args, **kw)
    
    def handleCommand(self, command, prefix, params):
        if command in self.ircd.userCommands:
            handlers = self.ircd.userCommands[command]
            if not handlers:
                return
            data = None
            spewRegWarning = True
            for handler in handlers:
                if handler[0].forRegisteredUsers is not None:
                    if (handler[0].forRegisteredUsers is True and not self.isRegistered()) or (handler[0].forRegisteredUsers is False and self.isRegistered()):
                        continue
                spewRegWarning = False
                data = handler[0].parseParams(params, prefix)
                if data is not None:
                    break
            if data is None:
                if spewRegWarning:
                    if self.isRegistered() == 0:
                        self.sendMessage(irc.ERR_ALREADYREGISTERED, ":You may not reregister")
                    else:
                        self.sendMessage(irc.ERR_NOTREGISTERED, command, ":You have not registered")
                return
            actionName = "commandpermission-{}".format(command)
            if actionName in self.ircd.actions:
                permissionCount = 0
                for action in self.ircd.actions[actionName]:
                    result = action[0](self, command, data)
                    if result is True:
                        permissionCount += 1
                    elif result is False:
                        permissionCount -= 1
                    elif result is not None:
                        permissionCount += result
                if permissionCount < 0:
                    return
            actionName = "commandmodify-{}".format(command)
            if actionName in self.ircd.actions:
                for action in self.ircd.actions[actionName]:
                    newData = action[0](self, command, data)
                    if newData is not None:
                        data = newData
            for handler in handlers:
                if handler[0].execute(self, data):
                    if handler[0].resetsIdleTime:
                        self.idleSince = now()
                    break # If the command executor returns True, it was handled
            else:
                return # Don't process commandextra if it wasn't handled
            actionName = "commandextra-{}".format(command)
            if actionName in self.ircd.actions:
                for action in self.ircd.actions[actionName]:
                    action[0](self, command, data)
        else:
            suppressError = False
            if "commandunknown" in self.ircd.actions:
                for action in self.ircd.actions["commandunknown"]:
                    if action[0](self, command, params):
                        suppressError = True
            if not suppressError:
                self.sendMessage(irc.ERR_UNKNOWNCOMMAND, command, ":Unknown command")
    
    def connectionLost(self, reason):
        if self.uuid in self.ircd.users:
            self.disconnect("Connection reset")
        self.disconnectedDeferred.callback(None)
    
    def disconnect(self, reason):
        if self._pinger.running:
            self._pinger.stop()
        if self._registrationTimeoutTimer.active():
            self._registrationTimeoutTimer.cancel()
        del self.ircd.users[self.uuid]
        if self.isRegistered():
            del self.ircd.userNicks[self.nick]
        if "quit" in self.ircd.actions:
            for action in self.ircd.actions["quit"]:
                action[0](self, reason)
        channelList = copy(self.channels)
        for channel in channelList:
            self.leave(channel)
        self.transport.loseConnection()
    
    def _timeoutRegistration(self):
        if self.isRegistered():
            self._pinger.start(self.ircd.config.getWithDefault("user_ping_frequency", 60), False)
            return
        self.disconnect("Registration timeout")
    
    def _ping(self):
        if "pinguser" in self.ircd.actions:
            for action in self.ircd.actions["pinguser"]:
                action[0](self)
    
    def isRegistered(self):
        return not self._registerHolds
    
    def register(self, holdName):
        if holdName not in self._registerHolds:
            return
        self._registerHolds.remove(holdName)
        if not self._registerHolds:
            if self.nick in self.ircd.userNicks:
                self._registerHolds.add("NICK")
            if not self.ident or not self.gecos:
                self._registerHolds.add("USER")
            if self._registerHolds:
                return
            if "register" in self.ircd.actions:
                for action in self.ircd.actions["register"]:
                    if not action[0](self):
                        self.transport.loseConnection()
                        return
            self.sendMessage(irc.RPL_WELCOME, ":Welcome to the Internet Relay Chat Network {}".format(self.hostmask()))
            self.sendMessage(irc.RPL_YOURHOST, ":Your host is {}, running version {}".format(self.ircd.config["network_name"], version))
            self.sendMessage(irc.RPL_CREATED, ":This server was created {}".format(self.ircd.startupTime.replace(microsecond=0)))
            self.sendMessage(irc.RPL_MYINFO, self.ircd.config["network_name"], version, "".join(["".join(modes.keys()) for modes in self.ircd.userModes]), "".join(["".join(modes.keys()) for modes in self.ircd.channelModes]))
            isupportList = self.ircd.generateISupportList()
            isupportMsgList = splitMessage(" ".join(isupportList), 350)
            for line in isupportMsgList:
                self.sendMessage(irc.RPL_ISUPPORT, line, ":are supported by this server")
            if "welcome" in self.ircd.actions:
                for action in self.ircd.actions["welcome"]:
                    action[0](self)
    
    def addRegisterHold(self, holdName):
        if not self._registerHolds:
            return
        self._registerHolds.add(holdName)
    
    def hostmask(self):
        return "{}!{}@{}".format(self.nick, self.ident, self.host)
    
    def hostmaskWithRealHost(self):
        return "{}!{}@{}".format(self.nick, self.ident, self.realhost)
    
    def hostmaskWithIP(self):
        return "{}!{}@{}".format(self.nick, self.ident, self.ip)
    
    def changeNick(self, newNick):
        if newNick in self.ircd.userNicks:
            return
        oldNick = self.nick
        if oldNick:
            del self.ircd.userNicks[self.nick]
        self.nick = newNick
        self.ircd.userNicks[self.nick] = self.uuid
        self.nickSince = now()
        if self.isRegistered() and "changenick" in self.ircd.actions:
            for action in self.ircd.actions["changenick"]:
                action[0](self, oldNick)
    
    def changeIdent(self, newIdent):
        oldIdent = self.ident
        self.ident = newIdent
        if self.isRegistered() and "changeident" in self.ircd.actions:
            for action in self.ircd.actions["changeident"]:
                action[0](self, oldIdent)
    
    def changeHost(self, newHost):
        oldHost = self.host
        self.host = newHost
        if self.isRegistered() and "changehost" in self.ircd.actions:
            for action in self.ircd.actions["changehost"]:
                action[0](self, oldHost)
    
    def resetHost(self):
        self.changeHost(self.realhost)
    
    def changeGecos(self, newGecos):
        oldGecos = self.gecos
        self.gecos = newGecos
        if self.isRegistered() and "changegecos" in self.ircd.actions:
            for action in self.ircd.actions["changegecos"]:
                action[0](self, oldGecos)
    
    def setMetadata(self, namespace, key, value):
        if namespace not in self.metadata:
            return
        oldValue = None
        if key in self.metadata[namespace]:
            oldValue = self.metadata[namespace][key]
        if value == oldValue:
            return # Don't do any more processing, including calling the action
        if value is None:
            if key in self.metadata[namespace]:
                del self.metadata[namespace][key]
        else:
            self.metadata[namespace][key] = value
        if "usermetadataupdate" in self.ircd.actions:
            for action in self.ircd.actions["usermetadataupdate"]:
                action[0](self, namespace, key, oldValue, value)
    
    def joinChannel(self, channel, override = False):
        if not override:
            if "joinpermission" in self.ircd.actions:
                permissionCount = 0
                for action in self.ircd.actions["joinpermission"]:
                    vote = action[0](channel, self)
                    if vote is True:
                        permissionCount += 1
                    elif vote is False:
                        permissionCount -= 1
                if permissionCount < 0:
                    return
        if channel.name not in self.ircd.channels:
            self.ircd.channels[channel.name] = channel
            if "channelcreate" in self.ircd.actions:
                for action in self.ircd.actions["channelcreate"]:
                    action[0](channel)
        channel.users[self] = ""
        self.channels.append(channel)
        if "joinmessage" in self.ircd.actions:
            messageUsers = channel.users.keys()
            for action in self.ircd.actions["joinmessage"]:
                actions[0](channel, self, messageUsers)
                if not messageUsers:
                    break
        if "join" in self.ircd.actions:
            for action in self.ircd.actions["join"]:
                action[0](channel, self)
    
    def leaveChannel(self, channel):
        if channel not in self.channels:
            return
        if "leave" in self.ircd.actions["leave"]:
            for action in self.ircd.actions["leave"]:
                action[0](channel, self)
        self.channels.remove(channel)
        del channel.users[self]
        if not channel.users:
            keepChannel = False
            if "channeldestroyorkeep" in self.ircd.actions:
                for action in self.ircd.actions["channeldestroyorkeep"]:
                    if action[0](channel):
                        keepChannel = True
                        break
            if not keepChannel:
                if "channeldestory" in self.ircd.actions:
                    for action in self.ircd.actions["channeldestroy"]:
                        action[0](channel)
                del self.ircd.channels[channel.name]
    
    def setMode(self, user, modeString, params, source = None):
        adding = True
        changing = []
        for mode in modeString:
            if len(changing) >= 20:
                break
            if mode == "+":
                adding = True
                continue
            if mode == "-":
                adding = False
                continue
            if mode not in self.ircd.userModeTypes:
                continue
            param = None
            modeType = self.ircd.userModeTypes[mode]
            if modeType in (ModeType.List, ModeType.ParamOnUnset) or (modeType == ModeType.Param and adding):
                try:
                    param = params.pop(0)
                except IndexError:
                    continue
            paramList = [None]
            if param:
                if adding:
                    paramList = self.ircd.userModes[modeType][mode].checkSet(param)
                else:
                    paramList = self.ircd.userModes[modeType][mode].checkUnset(param)
            if paramList is None:
                continue
            del param # We use this later
            
            if user:
                source = None
            
            for param in paramList:
                if len(changing) >= 20:
                    break
                if user and "modepermission-user-{}".format(mode) in self.ircd.actions:
                    permissionCount = 0
                    for action in self.ircd.actions["modepermission-user-{}".format(mode)]:
                        vote = action[0](self, user, mode, param)
                        if vote is True:
                            permissionCount += 1
                        else:
                            permissionCount -= 1
                    if permissionCount < 0:
                        continue
                if adding:
                    if modeType == ModeType.List:
                        if mode not in self.modes:
                            self.modes[mode] = []
                        found = False
                        for data in self.modes[mode]:
                            if data[0] == param:
                                found = True
                                break
                        if found:
                            continue
                        self.modes[mode].append((param, user, source, now()))
                    else:
                        if mode not in self.modes or self.modes[mode] == param:
                            continue
                        self.modes[mode] = param
                else:
                    if mode not in self.modes:
                        continue
                    if modeType == ModeType.List:
                        for index, data in enumerate(self.modes[mode]):
                            if data[0] == param:
                                del self.modes[mode][index]
                                break
                        else:
                            continue
                    else:
                        if mode in self.modes:
                            del self.modes[mode]
                        else:
                            continue
                changing.append((adding, mode, param, user, source))
                if "modechange-user-{}".format(mode) in self.ircd.actions:
                    for action in self.ircd.actions["modechange-user-{}".format(mode)]:
                        action[0](self, adding, mode, param, user, source)
        if changing and "modechanges-user" in self.ircd.actions:
            for action in self.ircd.actions["modechanges-user"]:
                action[0](self, changing)
        return changing

class RemoteUser(IRCUser):
    def __init__(self, ircd, ip, uuid = None, host = None):
        IRCUser.__init__(self, ircd, ip, uuid, host)
        self._registrationTimeoutTimer.cancel()
    
    def sendMessage(self, command, *params, **kw):
        if self.uuid[:3] not in self.ircd.servers:
            raise RuntimeError ("The server for this user isn't registered in the server list!")
        if "prefix" not in kw:
            kw["prefix"] = self.ircd.serverID
        elif kw["prefix"] is None:
            del kw["prefix"]
        to = self.nick
        if "to" in kw:
            to = kw["to"]
            del kw["to"]
        if to:
            paramList = (to,) + params
        else:
            paramList = params
        if "sendremoteusermessage" in self.ircd.actions:
            for action in self.ircd.actions["sendremoteusermessage"]:
                if action[0](self, command, *params, **kw):
                    break
    
    def register(self, holdName, fromRemote = False):
        if not fromRemote:
            return
        if holdName not in self._registerHolds:
            return
        self._registerHolds.remove(holdName)
        if not self._registerHolds:
            if "remoteregister" in self.ircd.actions:
                for action in self.ircd.actions["remoteregister"]:
                    action[0](self)
    
    def addRegisterHold(self, holdName):
        pass # We're just not going to allow this here.
    
    def disconnect(self, reason, fromRemote = False):
        if fromRemote:
            if self.isRegistered():
                del self.ircd.userNicks[self.nick]
            del self.ircd.users[self.uuid]
            if "remotequit" in self.ircd.actions:
                for action in self.ircd.actions["remotequit"]:
                    if action[0](self, reason):
                        break
        else:
            if "remotequitrequest" in self.ircd.actions:
                for action in self.ircd.actions["remotequitrequest"]:
                    if action[0](self, reason):
                        break
    
    def changeNick(self, newNick, fromRemote = False):
        if fromRemote:
            oldNick = self.nick
            del self.ircd.userNicks[self.nick]
            self.nick = newNick
            self.ircd.userNicks[self.nick] = self.uuid
            if "remotechangenick" in self.ircd.actions:
                for action in self.ircd.actions["remotechangenick"]:
                    action[0](self, oldNick)
        else:
            if "remotenickrequest" in self.ircd.actions:
                for action in self.ircd.actions["remotenickrequest"]:
                    if action[0](self, newNick):
                        break
    
    def changeIdent(self, newIdent, fromRemote = False):
        if fromRemote:
            oldIdent = self.ident
            self.ident = newIdent
            if "remotechangeident" in self.ircd.actions:
                for action in self.ircd.actions["remotechangeident"]:
                    action[0](self, newIdent)
        else:
            if "remoteidentrequest" in self.ircd.actions:
                for action in self.ircd.actions["remoteidentrequest"]:
                    if action[0](self, newIdent):
                        break
    
    def changeHost(self, newHost, fromRemote = False):
        if fromRemote:
            oldHost = self.host
            self.host = newHost
            if "remotechangehost" in self.ircd.actions:
                for action in self.ircd.actions["remotechangehost"]:
                    action[0](self, oldHost)
        else:
            if "remotehostrequest" in self.ircd.actions:
                for action in self.ircd.actions["remotehostrequest"]:
                    if action[0](self, newHost):
                        break
    
    def changeGecos(self, newGecos, fromRemote = False):
        if fromRemote:
            oldGecos = self.gecos
            self.gecos = newGecos
            if "remotechangegecos" in self.ircd.actions:
                for action in self.ircd.actions["remotechangegecos"]:
                    action[0](self, oldGecos)
        else:
            if "remotegecosrequest" in self.ircd.actions:
                for action in self.ircd.actions["remotegecosrequest"]:
                    if action[0](self, newGecos):
                        break
    
    def joinChannel(self, channel, override = False, fromRemote = False):
        if fromRemote:
            if channel.name not in self.ircd.channels:
                self.ircd.channels[channel.name] = channel
                if "channelcreate" in self.ircd.actions:
                    for action in self.ircd.actions["channelcreate"]:
                        action[0](channel)
            channel.users[self] = ""
            self.channels.append(channel)
            if "joinmessage" in self.ircd.actions:
                messageUsers = channel.users.keys()
                for action in self.ircd.actions["joinmessage"]:
                    actions[0](channel, self, messageUsers)
                    if not messageUsers:
                        break
            if "remotejoin" in self.ircd.actions:
                for action in self.ircd.actions["remotejoin"]:
                    action[0](channel, self)
        else:
            if "remotejoinrequest" in self.ircd.actions:
                for action in self.ircd.actions["remotejoinrequest"]:
                    if action[0](self, channel):
                        break
    
    def leaveChannel(self, channel, fromRemote = False):
        if fromRemote:
            if "remoteleave" in self.ircd.actions["remoteleave"]:
                for action in self.ircd.actions["remoteleave"]:
                    action[0](channel, self)
            self.channels.remove(channel)
            del channel.users[self]
            if not channel.users:
                keepChannel = False
                if "channeldestroyorkeep" in self.ircd.actions:
                    for action in self.ircd.actions["channeldestroyorkeep"]:
                        if action[0](channel):
                            keepChannel = True
                            break
                if not keepChannel:
                    if "channeldestroy" in self.ircd.actoins:
                        for action in self.ircd.actions["channeldestroy"]:
                            action[0](channel)
                    del self.ircd.channels[channel.name]
        else:
            if "remoteleaverequest" in self.ircd.actions:
                for action in self.ircd.actoins["remoteleaverequest"]:
                    if action[0](self, channel):
                        break

class LocalUser(IRCUser):
    """
    LocalUser is a fake user created by a module, which is not
    propagated to other servers.
    """
    def __init__(self, ircd, ip, host = None):
        IRCUser.__init__(self, ircd, ip, None, host)
        self.localOnly = True
        self._sendMsgFunc = lambda self, command, *args, **kw: None
        self._registrationTimeoutTimer.cancel()
    
    def setSendMsgFunc(self, func):
        self._sendMsgFunc = func
    
    def sendMessage(self, command, *args, **kw):
        self._sendMsgFunc(self, command, *args, **kw)
    
    def handleCommand(self, command, prefix, params):
        if command not in self.ircd.userCommands:
            raise ValueError ("Command not loaded")
        handlers = self.ircd.userCommands[command]
        if not handlers:
            return
        data = None
        for handler in handlers:
            if handler[0].forRegisteredUsers is False:
                continue
            data = handler[0].parseParams(params, prefix)
            if data is not None:
                break
        if data is None:
            return
        actionName = "commandmodify-{}".format(command)
        if actionName in self.ircd.actions:
            for action in self.ircd.actions[actionName]:
                newData = action[0](self, command, data)
                if newData is not None:
                    data = newData
        for handler in handlers:
            if handler[0].execute(self, data):
                if handler[0].resetsIdleTime:
                    self.idleSince = now()
                break
        else:
            return
        actionName = "commandextra-{}".format(command)
        if actionName in self.ircd.actions:
            for action in self.ircd.actions[actionName]:
                action[0](self, command, data)
    
    def disconnect(self, reason):
        del self.ircd.users[self.uuid]
        del self.ircd.userNicks[self.nick]
        if "localquit" in self.ircd.actions:
            for action in self.ircd.actions["localquit"]:
                action[0](self, reason)
        channelList = copy(self.channels)
        for channel in channelList:
            self.leave(channel)
    
    def register(self, holdName):
        if holdName not in self._registerHolds:
            return
        self._registerHolds.remove(holdName)
        if not self._registerHolds:
            if "localregister" in self.ircd.actions:
                for action in self.ircd.actions["localregister"]:
                    action[0](self)
    
    def joinChannel(self, channel, override = False):
        IRCUser.joinChannel(self, channel, True)