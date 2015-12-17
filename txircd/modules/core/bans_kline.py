from twisted.plugin import IPlugin
from twisted.words.protocols import irc
from txircd.config import ConfigValidationError
from txircd.module_interface import Command, ICommand, IModuleData, ModuleData
from txircd.modules.xlinebase import XLineBase
from txircd.utils import durationToSeconds, ircLower, now
from zope.interface import implements
from fnmatch import fnmatchcase

class KLine(ModuleData, Command, XLineBase):
	implements(IPlugin, IModuleData, ICommand)
	
	name = "KLine"
	core = True
	lineType = "K"
	propagateToServers = False
	
	def actions(self):
		return [ ("register", 10, self.checkLines),
		         ("commandpermission-KLINE", 10, self.restrictToOper),
		         ("statsruntype-klines", 10, self.generateInfo),
		         ("burst", 10, self.burstLines) ]
	
	def userCommands(self):
		return [ ("KLINE", 1, self) ]
	
	def load(self):
		self.initializeLineStorage()

	def verifyConfig(self, config):
		if "client_ban_msg" in config and not isinstance(config["client_ban_msg"], basestring):
			raise ConfigValidationError("client_ban_msg", "value must be a string")
	
	def checkUserMatch(self, user, mask, data):
		banMask = self.normalizeMask(mask)
		userMask = ircLower("{}@{}".format(user.ident, user.host()))
		if fnmatchcase(userMask, banMask):
			return True
		userMask = ircLower("{}@{}".format(user.ident, user.realHost))
		if fnmatchcase(userMask, banMask):
			return True
		userMask = ircLower("{}@{}".format(user.ident, user.ip))
		if fnmatchcase(userMask, banMask):
			return True
		return False
	
	def killUser(self, user, reason):
		self.ircd.log.info("Matched user {user.uuid} ({user.ident}@{user.host()}) against a k:line: {reason}", user=user, reason=reason)
		user.sendMessage(irc.ERR_YOUREBANNEDCREEP, self.ircd.config.get("client_ban_msg", "You're banned! Email abuse@example.com for assistance."))
		user.disconnect("K:Lined: {}".format(reason))
	
	def checkLines(self, user):
		banReason = self.matchUser(user)
		if banReason:
			self.killUser(user, banReason)
			return False
		return True
	
	def restrictToOper(self, user, data):
		if not self.ircd.runActionUntilValue("userhasoperpermission", user, "command-kline", users=[user]):
			user.sendMessage(irc.ERR_NOPRIVILEGES, "Permission denied - You do not have the correct operator privileges")
			return False
		return None
	
	def parseParams(self, user, params, prefix, tags):
		if len(params) < 1 or len(params) == 2:
			user.sendSingleError("KLineParams", irc.ERR_NEEDMOREPARAMS, "KLINE", "Not enough parameters")
			return None
		
		banmask = params[0]
		if banmask in self.ircd.userNicks:
			targetUser = self.ircd.users[self.ircd.userNicks[banmask]]
			banmask = "{}@{}".format(targetUser.ident, targetUser.host())
		else:
			if "@" not in banmask:
				banmask = "*@{}".format(banmask)
		if len(params) == 1:
			return {
				"mask": banmask
			}
		return {
			"mask": banmask,
			"duration": durationToSeconds(params[1]),
			"reason": " ".join(params[2:])
		}
	
	def execute(self, user, data):
		banmask = data["mask"]
		if "reason" in data:
			if not self.addLine(banmask, now(), data["duration"], user.hostmask(), data["reason"]):
				user.sendMessage("NOTICE", "*** K:Line for {} is already set.".format(banmask))
			badUsers = []
			for checkUser in self.ircd.users.itervalues():
				reason = self.matchUser(checkUser)
				if reason:
					badUsers.append((checkUser, reason))
			for badUser in badUsers:
				self.killUser(*badUser)
			if data["duration"] > 0:
				user.sendMessage("NOTICE", "*** Timed k:line for {} has been set, to expire in {} seconds.".format(banmask, data["duration"]))
			else:
				user.sendMessage("NOTICE", "*** Permanent k:line for {} has been set.".format(banmask))
			return True
		if not self.delLine(banmask):
			user.sendMessage("NOTICE", "*** K:Line for {} doesn't exist.".format(banmask))
			return True
		user.sendMessage("NOTICE", "*** K:Line for {} has been removed.".format(banmask))
		return True

klineModule = KLine()