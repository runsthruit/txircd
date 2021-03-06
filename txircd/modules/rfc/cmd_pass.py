from twisted.plugin import IPlugin
from twisted.words.protocols import irc
from txircd.config import ConfigValidationError
from txircd.module_interface import Command, ICommand, IModuleData, ModuleData
from zope.interface import implements

class PassCommand(ModuleData, Command):
	implements(IPlugin, IModuleData, ICommand)
	
	name = "PassCommand"
	core = True
	forRegistered = False
	
	def actions(self):
		return [ ("register", 10, self.matchPassword) ]
	
	def userCommands(self):
		return [ ("PASS", 1, self) ]

	def verifyConfig(self, config):
		if "server_password" in config and not isinstance(config["server_password"], basestring):
			raise ConfigValidationError("server_password", "value must be a string")
	
	def parseParams(self, user, params, prefix, tags):
		if not params:
			user.sendSingleError("PassCmd", irc.ERR_NEEDMOREPARAMS, "PASS", "Not enough parameters")
			return None
		return {
			"password": params[0]
		}
	
	def execute(self, user, data):
		user.cache["password"] = data["password"]
		return True
	
	def matchPassword(self, user):
		try:
			serverPass = self.ircd.config["server_password"]
		except KeyError:
			return True
		if "password" not in user.cache or serverPass != user.cache["password"]:
			user.sendMessage("ERROR", "Closing Link: {}@{} [Access Denied]".format(user.ident, user.host()), to=None, prefix=None)
			return False
		return True

passCmd = PassCommand()