from twisted.plugin import IPlugin
from twisted.words.protocols import irc
from txircd.module_interface import Command, ICommand, IModuleData, ModuleData
from txircd.utils import splitMessage
from zope.interface import implements

class NamesCommand(ModuleData, Command):
    implements(IPlugin, IModuleData, ICommand)
    
    name = "NamesCommand"
    core = True
    
    def hookIRCd(self, ircd):
        self.ircd = ircd
    
    def userCommands(self):
        return [ ("NAMES", 1, self) ]
    
    def actions(self):
        return [ ("join", 2, self.namesOnJoin) ]
    
    def namesOnJoin(self, channel, user):
        self.execute(user, { "channels": [ channel ] })
    
    def parseParams(self, user, params, prefix, tags):
        chanNames = params[0].split(",") if params else []
        channels = []
        for chanName in chanNames:
            if chanName in self.ircd.channels:
                channels.append(self.ircd.channels[chanName])
            else:
                user.sendMessage(irc.ERR_NOSUCHCHANNEL, chanName, ":No such channel")
        return {
            "channels": channels
        }
    
    def execute(self, user, data):
        chanList = data["channels"]
        if not chanList:
            user.sendMessage(irc.RPL_ENDOFNAMES, "*", ":End of /NAMES list")
            return True
        for channel in chanList:
            showChannelUsers = []
            for chanUser in channel.users.iterkeys():
                if self.ircd.runActionVoting("showchanneluser", channel, user, chanUser, users=[user, chanUser], channels=[channel]) < 0:
                    continue
                showAs = self.ircd.runActionUntilValue("displaychanneluser", channel, chanUser, users=[chanUser], channels=[channel])
                if not showAs:
                    showAs = chanUser.nick
                showChannelUsers.append("{}{}".format(self.ircd.runActionUntilValue("channelstatuses", channel, chanUser, users=[chanUser], channels=[channel]), showAs))
            if showChannelUsers:
                userLines = splitMessage(" ".join(showChannelUsers), 300)
                for line in userLines:
                    user.sendMessage(irc.RPL_NAMREPLY, "=", channel.name, ":{}".format(line))
            user.sendMessage(irc.RPL_ENDOFNAMES, channel.name, ":End of /NAMES list")
        return True

namesCmd = NamesCommand()