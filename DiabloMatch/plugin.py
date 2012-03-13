###
# Copyright (c) 2012, listen2, Chaosteil
# All rights reserved.
#
#
###

import supybot.utils as utils
from supybot.commands import *
import supybot.plugins as plugins
import supybot.ircutils as ircutils
import supybot.ircmsgs as ircmsgs
import supybot.callbacks as callbacks

from sqlalchemy import create_engine, Table, MetaData, func, or_
from sqlalchemy.orm import sessionmaker, mapper
from sqlalchemy.orm.exc import NoResultFound, MultipleResultsFound
from sqlalchemy.sql import expression

import time, pytz
import re
from datetime import datetime
import hashlib

import sys
if "/home/diablobot/dbot/plugins/DiabloCommon" not in sys.path:
     sys.path.append("/home/diablobot/dbot/plugins/DiabloCommon")
import DiabloCommon

class User(object):
    quickfields = [
        ("irc_name", "IRC"),
        ("reddit_name", "Reddit"),
        ("steam_name", "Steam*"),
        ("bt", "Battletag*")
    ]

    def __init__(self):
        pass

    def __repr__(self):
        return "<User('%s')>" % (self.irc_name)

    def pretty_print(self, r=True):
        out = ""
        for f in User.quickfields:
            v = getattr(self, f[0])
            if v != None and v != 0:
                out += "" + f[1] + ": " + v + ", "
        out = out[:-2]
        if r and self.realm != None:
            out += ", Realm: " + self.realm
        return out

    def full_print(self):
        out = []
        out.append(self.pretty_print(r=False))
        if self.realm != None:
            out.append("Realm: " + self.realm)
        if self.tz != None:
            tz_to = pytz.timezone(self.tz)
            tz_from = pytz.timezone("America/New_York")
            tm = datetime.now().replace(tzinfo=tz_from)
            tm_to = tm.astimezone(tz_to)
            out.append("Local time: " + tm_to.strftime("%d %b %H:%M:%S (%Z %z)"))
        if self.cmt != None:
            out.append("Comment: " + self.cmt)
        if self.url != None:
            out.append("URL: " + self.url)
        return out

#engine = create_engine('sqlite:///plugins/DiabloMatch/db.sqlite3', echo=True)
engine = create_engine('sqlite:///plugins/DiabloMatch/db.sqlite3')
Session = sessionmaker(bind=engine)
meta = MetaData()
meta.bind = engine
user_table = Table('users', meta, autoload=True)
mapper(User, user_table)

class DiabloMatch(callbacks.Plugin):
    """Add the help for "@plugin help DiabloMatch" here
    This should describe *how* to use this plugin."""

    # TODO fix when the actual list is made available
    _realms = [
        "useast",
        "uswest",
        "europe",
        "asia"
    ]

    _bt_regexp    = re.compile(r"\w{1,32}#\d{4,8}$")
    _color_regexp = re.compile("(?:(?:\d{1,2}(?:,\d{1,2})?)?|||)")

    def _get_services_account(self, irc, nick):
        # Is nick in Whois?
        if nick not in DiabloCommon.whois.keys():
            irc.queueMsg(ircmsgs.whois(nick, nick))
            DiabloCommon.whois[nick] = None    #None means whois in process
            return (1, )
        
        # Whois in progress
        elif DiabloCommon.whois[nick] == None:
            return (2, )

        # User not authenticated with NickServ
        elif DiabloCommon.whois[nick] == -1:
            # We try to refresh the auth , maybe the user is registered now
            irc.queueMsg(ircmsgs.whois(nick, nick))
            return (3, )

        # User authenticated some time ago
        else:    
            # Ten hours since auth, we refresh the auth
            if time.time() - DiabloCommon.whois[nick][1] > 36000:
                irc.queueMsg(ircmsgs.whois(nick, nick))
                return (4, DiabloCommon.whois[nick][0])

            # User logged in
            else:
                return (5, DiabloCommon.whois[nick][0])

    def _check_auth(self, irc, msg):
        a = self._get_services_account(irc, msg.nick)
        if a[0] == 1:
            irc.reply("Sorry, I needed to verify your identity. "
                      "Please repeat your previous command.", private=True)
        elif a[0] == 2:
            irc.reply("Still verifying your identity. "
                      "Try again in a few seconds.", private=True)
        elif a[0] == 3:
            irc.reply("You're not logged in. Please authenticate with "
                      "NickServ so I know who you are.", private=True)
        elif a[0] == 4:
            irc.reply("You were logged in to NickServ as '%s', but your "
                      "last session expired. Please repeat your previous "
                      "command." % a[1], private=True)
        elif a[0] == 5:
            irc.reply("You're logged in to NickServ as '%s'." % a[1],
                      private=True)
            return a[1]
        else:
            irc.reply("This can't ever happen. "
                      "Someone must have divided by zero.", private=True)
        return False

    # "Logged in as" WHOIS response
    def do330(self, irc, msg):
        nick = msg.args[1]
        account = msg.args[2]
        DiabloCommon.whois[nick] = (account, time.time())

    # End of WHOIS responses
    def do318(self, irc, msg):
        # If we get this and didn't get a 330, then the user is not logged in
        nick = msg.args[1]
        if DiabloCommon.whois[nick] == None:
            DiabloCommon.whois[nick] = -1 #-1 means whois complete and not logged in

    def _btRegister(self, irc, msg, battletag):
        if battletag:
            self.btset(irc, msg, ["bt", battletag])
        else:
            irc.reply("Please specify the battletag you wish to register: "
                      "!bt register BattleTag#1234", private=True)

    def _findBtUsers(self, irc, name, typename):
        session = Session()

        datatypes_pretty = {
            "bt":      (User.bt, "BattleTag"),
            "reddit":  (User.reddit_name, "Reddit Username"),
            "email":   (User.email, "Email Address"),
            "irc":     (User.irc_name, "IRC Services Username"),
            "steam":   (User.steam_name, "Steam Username")
        }

        # A small helper closure
        def show_result(datatype, count):
            irc.reply("Looking up user %s (%s). %d result%s. "
                      "Use !btinfo <user> for details." %
                      (name, datatype , count,
                       "s" if not users.count() == 1 else ""),
                     private=True)

        if typename in datatypes_pretty.keys():
            users = session.query(User).filter(
                func.lower(datatypes_pretty[typename][0]).like(
                    func.lower(name.replace("*", "%"))))
            show_result(datatypes_pretty[typename][1], users.count())

        elif typename == None:
            users = session.query(User).filter(or_(
                    func.lower(User.bt).like(
                        func.lower(name.replace("*", "%"))),
                    func.lower(User.reddit_name).like(
                        func.lower(name.replace("*", "%"))),
                    func.lower(User.email).like(
                        func.lower(name.replace("*", "%"))),
                    func.lower(User.irc_name).like(
                        func.lower(name.replace("*", "%"))),
                    func.lower(User.steam_name).like(
                        func.lower(name.replace("*", "%")))))
            show_result("All fields", users.count())

        else:
            irc.reply("I don't recognize that field. Known fields: "
                      "bt, reddit, email, irc, steam",
                      private=True)
            users = []

        return users

    def bt(self, irc, msg, args, arg1, arg2):
        """[\37user]  |  register \37Battletag#1234
        Shows user information. \37user may be prefixed with irc:, steam:, reddit:, email:, or bt:, and may contain the wildcard *. If \37user is not supplied, your own information will be displayed.
        If the first argument is register, the given \37battletag will be registered as yours.
        """

        if arg1 == "register":
            self._btRegister(irc, msg, arg2)
        elif arg1 == None:
            s = self._check_auth(irc, msg)
            if s:
                session = Session()
                try:
                    # We pick one user. irc_name is unique, so no worries
                    user = session.query(User).filter(
                        func.lower(User.irc_name) == func.lower(s)).one()
                    irc.reply("Your battletag is %s" % user.pretty_print(),
                              private=True)

                except NoResultFound:
                    irc.reply("No battletag found for you. Register one with "
                              "!bt register BattleTag#1234", private=True)
        else:
            data = arg1.split(":")

            if len(data) == 1:
                users = self._findBtUsers(irc, data[0], None)
            else:
                users = self._findBtUsers(irc, data[1], data[0])

            for user in users:
                irc.reply(user.pretty_print(), private=True)

    bt = wrap(bt, [optional('anything'), optional('anything')])

    def btinfo(self, irc, msg, args, arg1):
        """[\37user]
        Shows detailed user information. \37user may be prefixed with irc:, steam:, reddit:, email:, or bt:, and may contain the wildcard *. If \37user is not supplied, your own information will be displayed.
        """
        data = arg1.split(":")

        if len(data) == 1:
            users = self._findBtUsers(irc, data[0], "irc")
        else:
            users = self._findBtUsers(irc, data[1], data[0])

        for user in users:
            irc.reply("User details. Fields marked with a * are not "
                      "officially validated.", private=True)
            for line in user.full_print():
                irc.reply(line, private=True)

    btinfo = wrap(btinfo, [optional('anything')])

    def _check_registered(self, irc, msg, session, ircname):
        try:
            user = session.query(User).filter(
                func.lower(User.irc_name) == func.lower(ircname)).one()
        except NoResultFound:
            irc.reply("Register a battletag first.", private=True)
            return None
        return user

    def btset(self, irc, msg, args, arg1, arg2):
        """\37field \37value
        Modifies your user info. Invoke btset list to see a list of available fields
        """
        try:
            arg1 = DiabloMatch._color_regexp.sub("", arg1)
            arg2 = DiabloMatch._color_regexp.sub("", arg2)
        except:
            pass
        if arg1.lower() == "list":    #or arg1.lower() not in []:
            irc.reply("Available fields: bt/battletag, reddit_name, email, irc_name, steam_name, password, comment, tz/timezone, realm, url", private=True)
            return
        if arg2 == None:
            irc.reply("Here's the current value of " + arg1 + ": (not yet implemented).", private=True)
            return
        ircname = self._check_auth(irc, msg)
        if not ircname:
            return
        if arg1.lower() in ["bt", "battletag"]:
            if DiabloMatch._bt_regexp.match(arg2) == None:
                irc.reply("That's not a proper battletag. Use 'BattleTag#1234' format.", private=True)
                return
            session = Session()
            try:
                user = session.query(User).filter(func.lower(User.irc_name) == func.lower(ircname)).one()
            except NoResultFound:    #we want irc_name to be unique, even though it's not a primary key
                user = User()
                user.irc_name = ircname
            user.bt = arg2
            session.add(user)
            session.commit()

            irc.reply("Registered your battletag as %s" % arg2,
                      private=True)
        elif arg1.lower() in ["tz", "timezone"]:
            session = Session()
            user = self._check_registered(irc, msg, session, ircname)
            if user == None:
                return
            try:
                pytz.timezone(arg2)
            except pytz.UnknownTimeZoneError as e:
                irc.reply("Unknown time zone %s" % str(e), private=True)
                irc.reply("You can find a list of valid time zones at "
                          "http://en.wikipedia.org/wiki/List_of_tz_database_time_zones",
                          private=True)
                return
            user.tz = expression.null() if arg2 == "" else arg2
            session.add(user)
            session.commit()
            irc.reply("Set timezone to " + arg2 + ".", private=True)
        elif arg1.lower() == "realm":
            if arg2 not in DiabloMatch._realms:
                irc.reply("That's not a valid realm. Valid realms: " + ", ".join(DiabloMatch._realms) + ".", private=True)
                return
            session = Session()
            user = self._check_registered(irc, msg, session, ircname)
            if user == None:
                return
            user.realm = expression.null() if arg2 == "" else arg2
            session.add(user)
            session.commit()
            irc.reply("Set realm to " + arg2 + ".", private=True)
        elif arg1.lower() in ["steam", "steam_name"]:
            session = Session()
            user = self._check_registered(irc, msg, session, ircname)
            if user == None:
                return
            user.steam_name = expression.null() if arg2 == "" else arg2
            session.add(user)
            session.commit()
            irc.reply("Set steam_name to " + arg2 + ".", private=True)
        elif arg1.lower() == "password":
            session = Session()
            user = self._check_registered(irc, msg, session, ircname)
            if user == None:
                return
            hasher = hashlib.sha256()
            hasher.update(arg2)
            user.password = expression.null() if arg2 == "" else hasher.hexdigest()
            session.add(user)
            session.commit()
            irc.reply("Set password.", private=True)
        elif arg1.lower() == "email":
            session = Session()
            user = self._check_registered(irc, msg, session, ircname)
            if user == None:
                return
            user.email = expression.null() if arg2 == "" else arg2
            session.add(user)
            session.commit()
            irc.reply("Set email address to " + arg2 + ".", private=True)
        elif arg1.lower() == "comment":
            session = Session()
            user = self._check_registered(irc, msg, session, ircname)
            if user == None:
                return
            user.cmt = expression.null() if arg2 == "" else arg2
            session.add(user)
            session.commit()
            irc.reply("Set comment to " + arg2 + ".", private=True)
        elif arg1.lower() == "url":
            session = Session()
            user = self._check_registered(irc, msg, session, ircname)
            if user == None:
                return
            user.url = expression.null() if arg2 == "" else arg2
            session.add(user)
            session.commit()
            irc.reply("Set URL to " + arg2 + ".", private=True)
    btset = wrap(btset, ['anything', optional('text')])

    #on any channel activity, cache the user's whois info
    def doPrivmsg(self, irc, msg):
        if ircmsgs.isCtcp(msg) and not ircmsgs.isAction(msg):
            return
        if irc.isChannel(msg.args[0]):
            self._get_services_account(irc, msg.nick)

    #on any channel join, cache the user's whois info
    def doJoin(self, irc, msg):
        if irc.isChannel(msg.args[0]):
            self._get_services_account(irc, msg.nick)

Class = DiabloMatch
