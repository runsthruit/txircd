from twisted.plugin import IPlugin
from twisted.words.protocols import irc
from txircd.module_interface import Command, ICommand, IModuleData, ModuleData
from zope.interface import implements

class GlobopsCommand(Command, ModuleData):
	implements(IPlugin, IModuleData, ICommand)

	name = "Globops"

	def actions(self):
		return [ ("commandpermission-GLOBOPS", 1, self.restrictToOpers) ]

	def userCommands(self):
		return [ ("GLOBOPS", 1, self) ]

	def restrictToOpers(self, user, data):
		if not self.ircd.runActionUntilValue("userhasoperpermission", user, "command-globops", users=[user]):
			user.sendMessage(irc.ERR_NOPRIVILEGES, "Permission denied - You do not have the correct operator privileges")
			return False
		return None

	def parseParams(self, user, params, prefix, tags):
		if not params:
			user.sendSingleError("GlobopsParams", irc.ERR_NEEDMOREPARAMS, "GLOBOPS", "Not enough parameters")
			return None
		return {
			"message": " ".join(params)
		}

	def execute(self, user, data):
		for targetUser in self.ircd.users.itervalues():
			if user == targetUser:
				continue
			if not self.ircd.runActionUntilValue("userhasoperpermission", targetUser, "view-globops", users=[user]):
				continue
			targetUser.sendMessage("NOTICE", "*** GLOBOPS from {}: {}".format(user.nick, data["message"]))
		return True

globops = GlobopsCommand()