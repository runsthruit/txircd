from twisted.plugin import IPlugin
from twisted.words.protocols import irc
from txircd.module_interface import IModuleData, ModuleData
from zope.interface import implements

irc.RPL_WHOISACCOUNT = "330"

class WhoisAccount(ModuleData):
	implements(IPlugin, IModuleData)

	name = "WhoisAccount"
	core = True

	def actions(self):
		return [ ("extrawhois", 1, self.whoisAccountName) ]

	def whoisAccountName(self, user, targetUser):
		if targetUser.metadataKeyExists("accountname"):
			user.sendMessage(irc.RPL_WHOISACCOUNT, targetUser.nick, targetUser.metadataValue("accountname"), "is logged in as")

whoisAccount = WhoisAccount()