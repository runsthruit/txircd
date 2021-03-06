from twisted.plugin import IPlugin
from txircd.module_interface import Command, ICommand, IModuleData, ModuleData
from txircd.utils import ModeType, timestamp
from zope.interface import implements

class ServerBurst(ModuleData, Command):
	implements(IPlugin, IModuleData, ICommand)
	
	name = "ServerBurst"
	core = True
	forRegistered = False
	
	def actions(self):
		return [ ("burst", 100, self.startBurst),
		         ("burst", 1, self.completeBurst) ]
	
	def serverCommands(self):
		return [ ("BURST", 1, self) ]
	
	def startBurst(self, server):
		server.bursted = False
		serversByHopcount = []
		serversBurstingTo = []
		for remoteServer in self.ircd.servers.itervalues():
			if remoteServer == server:
				continue
			hopCount = 1
			servTrace = remoteServer
			if server == servTrace:
				serversBurstingTo.append(remoteServer.serverID)
				continue # Don't count this server
			burstingRemote = False
			while servTrace.nextClosest != self.ircd.serverID:
				servTrace = self.ircd.servers[servTrace.nextClosest]
				if server == servTrace:
					burstingRemote = True
					break
				hopCount += 1
			if burstingRemote:
				serversBurstingTo.append(remoteServer.serverID)
				continue
			while len(serversByHopcount) < hopCount:
				serversByHopcount.append([])
			serversByHopcount[hopCount - 1].append(remoteServer)
		for hopCount in range(1, len(serversByHopcount) + 1):
			strHopCount = str(hopCount)
			for remoteServer in serversByHopcount[hopCount - 1]:
				server.sendMessage("SERVER", remoteServer.name, remoteServer.serverID, strHopCount, remoteServer.nextClosest, remoteServer.description, prefix=self.ircd.serverID)
		for user in self.ircd.users.itervalues():
			if user.localOnly:
				continue
			if not user.isRegistered():
				continue
			if user.uuid[:3] in serversBurstingTo: # The remote server apparently already finished its burst (or at least enough that we know this), so we need to not send it those again.
				continue
			signonTimestamp = str(timestamp(user.connectedSince))
			nickTimestamp = str(timestamp(user.nickSince))
			modes = []
			params = []
			listModes = {}
			for mode, param in user.modes.iteritems():
				if self.ircd.userModeTypes[mode] == ModeType.List:
					listModes[mode] = param
				else:
					modes.append(mode)
					if param is not None:
						params.append(param)
			modeStr = "+{} {}".format("".join(modes), " ".join(params)) if params else "+{}".format("".join(modes))
			server.sendMessage("UID", user.uuid, signonTimestamp, user.nick, user.realHost, user.host(), user.currentHostType(), user.ident, user.ip, nickTimestamp, modeStr, user.gecos, prefix=self.ircd.serverID)
			sentListModes = False
			for mode, paramList in listModes.iteritems():
				for param, setter, time in paramList:
					server.sendMessage("LISTMODE", user.uuid, signonTimestamp, mode, param, setter, str(timestamp(time)), prefix=self.ircd.serverID)
					sentListModes = True
			if sentListModes:
				server.sendMessage("ENDLISTMODE", user.uuid, prefix=self.ircd.serverID)
			for key, value, visibility, setByUser in user.metadataList():
				server.sendMessage("METADATA", user.uuid, signonTimestamp, key, visibility, "1" if setByUser else "0", value, prefix=self.ircd.serverID)
		for channel in self.ircd.channels.itervalues():
			channelTimestamp = str(timestamp(channel.existedSince))
			users = []
			for user, data in channel.users.iteritems():
				if user.localOnly:
					continue
				if user.uuid[:3] in serversBurstingTo: # The remote server already knows about these users
					continue
				ranks = data["status"]
				users.append("{},{}".format(ranks, user.uuid))
			if not users:
				continue # Let's not sync this channel since it won't sync properly
			modes = []
			params = []
			listModes = {}
			for mode, param in channel.modes.iteritems():
				if self.ircd.channelModeTypes[mode] == ModeType.List:
					listModes[mode] = param
				else:
					modes.append(mode)
					if param is not None:
						params.append(param)
			modeStr = "+{} {}".format("".join(modes), " ".join(params)) if params else "+{}".format("".join(modes))
			fjoinParams = [channel.name, channelTimestamp] + modeStr.split(" ") + [" ".join(users)]
			server.sendMessage("FJOIN", *fjoinParams, prefix=self.ircd.serverID)
			sentListModes = False
			for mode, params in listModes.iteritems():
				for param, setter, time in params:
					server.sendMessage("LISTMODE", channel.name, channelTimestamp, mode, param, setter, str(timestamp(time)), prefix=self.ircd.serverID)
					sentListModes = True
			if sentListModes:
				server.sendMessage("ENDLISTMODE", channel.name, prefix=self.ircd.serverID)
			if channel.topic:
				server.sendMessage("TOPIC", channel.name, channelTimestamp, str(timestamp(channel.topicTime)), channel.topic, prefix=self.ircd.serverID)
			for key, value, visibility, setByUser in channel.metadataList():
				server.sendMessage("METADATA", channel.name, channelTimestamp, key, visibility, "1" if setByUser else "0", value, prefix=self.ircd.serverID)
	
	def completeBurst(self, server):
		server.sendMessage("BURST", prefix=self.ircd.serverID)
	
	def parseParams(self, server, params, prefix, tags):
		return {}
	
	def execute(self, server, data):
		server.endBurst()
		return True

serverBurst = ServerBurst()