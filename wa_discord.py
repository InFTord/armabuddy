# wa_discord.py
import asyncio
import logging
import re
from datetime import datetime, timezone

import discord


class WA_Discord(discord.Client):
    WA_Flags = {  # https://worms2d.info/WormNET_flags
         '0': ':flag_gb:',  '1': ':flag_ar:',  '2': ':flag_au:',  '3': ':flag_at:',  '4': ':flag_be:',
         '5': ':flag_br:',  '6': ':flag_ca:',  '7': ':flag_hr:',  '8': ':flag_ba:',  '9': ':flag_cy:',
        '10': ':flag_cz:', '11': ':flag_dk:', '12': ':flag_fi:', '13': ':flag_fr:', '14': ':flag_ge:',
        '15': ':flag_de:', '16': ':flag_gr:', '17': ':flag_hk:', '18': ':flag_hu:', '19': ':flag_is:',
        '20': ':flag_in:', '21': ':flag_id:', '22': ':flag_ir:', '23': ':flag_iq:', '24': ':flag_ie:',
        '25': ':flag_il:', '26': ':flag_it:', '27': ':flag_jp:', '28': ':flag_li:', '29': ':flag_lu:',
        '30': ':flag_my:', '31': ':flag_mt:', '32': ':flag_mx:', '33': ':flag_ma:', '34': ':flag_nl:',
        '35': ':flag_nz:', '36': ':flag_no:', '37': ':flag_pl:', '38': ':flag_pt:', '39': ':flag_pr:',
        '40': ':flag_ro:', '41': ':flag_ru:', '42': ':flag_sg:', '43': ':flag_za:', '44': ':flag_es:',
        '45': ':flag_se:', '46': ':flag_ch:', '47': ':flag_tr:', '48': ':flag_us:', '49': ':triangular_flag_on_post:'
    }

    def __init__(self, token: str, guilds: dict):
        # internal properties
        self.token = token  # discord token used for authenticating bot
        # dict containing settings on which guilds and channels we need to make sure exist
        self.settings = guilds
        self.guild_list = {}  # dict that will contain all references to channels and guilds
        self.logger = logging.getLogger('WA_Logger')
        self._intents = discord.Intents.default()
        self._intents.members = True
        self.prepared = False
        self.irc_reference = None
        self.forward_message = lambda x: x

        # embed config
        self.embed_gamelist_title = 'Currently active games in #AnythingGoes'
        self.embed_color = 0xffa300
        self.embed_icon = 'https://cdn.discordapp.com/emojis/501802399565086720.png?size=32'
        self.embed_footer = 'List last refreshed at'
        self.embed_default_flag = self.WA_Flags['49']
        self.embed_public_game = ':unlock:'
        self.embed_private_game = ':closed_lock_with_key:'
        self.embed_no_host = '*There are currently no games hosted on WormNET..* <:sadworm:883155422675087452>'
        self.embed_no_users = '*There are currently no users online on WormNET, something is probably horribly wrong.'

        # static messages
        self.bot_message_setup = 'I am trying to be a good bot, please bear with me while until I find all the cogs and gears!'
        self.bot_down_message = "I am sick right now and can't show you the game list at this moment."
        super().__init__(intents=self._intents)

    # HELPER FUNCTIONS #

    async def run(self):
        if self.is_closed() and self.is_ready():
            raise Exception(' * Unable to start bot.')

        self.logger.warning(' * Starting bot.')
        await self.start(self.token)

    async def stop(self):
        self.logger.warning(' * Stopping bot.')
        await self.close()

    # create an embed from WA_Gamelist response
    async def create_gamelist(self, games: list):
        embed = discord.Embed(title=self.embed_gamelist_title, colour=self.embed_color, timestamp=datetime.now(timezone.utc))
        # embed.set_thumbnail(url=self.embed_icon) # thumbnail does not fit if we want proper list
        embed.set_footer(text=self.embed_footer, icon_url=self.embed_icon)
        field = ''
        for game in games:
            append = flag = ''

            if game['packed_flag_id'] != '0':
                try:  # attempt to unpack ISO 3166-1 alpha-2 country code from LOWORD of last field of game response
                    flag = f':flag_{(int(game["packed_flag_id"]) & 0xFFFF).to_bytes(2, "little").decode("ascii").lower()}:'
                except UnicodeDecodeError:
                    pass

            if not flag:
                flag = self.WA_Flags.get(game['country'], self.embed_default_flag)

            append += self.embed_private_game if game['private'] == '1' else self.embed_public_game
            append += f'{discord.utils.escape_markdown(game["title"])} \n<wa://{game["host"]}?Scheme=Pf,Be&ID={game["gameid"]}>\n'
            append += f'Hosted by: {flag} {discord.utils.escape_markdown(game["user"])}\n\n'

            # fields can't be longer than 1024 characters, so better make sure we don't surpass limit..
            if len(field) + len(append) >= 1024:
                embed.add_field(name='Games', value=field, inline=False)
                field = ''
            field += append

        # if field is empty at this point there shouldn't be any open games, make sure we put placeholder description instead..
        if len(field) <= 0:
            embed.description = self.embed_no_host
        else:
            embed.add_field(name='Games', value=field, inline=False)
        return embed

    # make sure all our configured Guild(s) exist
    async def check_guilds(self):
        for guild in self.guilds:
            # if guild id is equal to id from settings file
            if guild.id in self.settings:
                self.logger.warning(f' * Found guild with name "{guild.name}"!')
                self.guild_list[guild.id] = {
                    'guild': guild,
                    'channels': self.settings[guild.id]['channels'],
                    'gamelist': self.settings[guild.id]['gamelist']
                }
            else:
                raise Exception(f'Could not find guild "{guild.name}" in settings.')

    # make sure all Guild(s) have the TextChannel(s) we have set up
    async def check_channels(self):
        # for every guild we have setup
        for settings in self.guild_list.values():
            guild = settings['guild']
            for channel in guild.text_channels:
                if channel.id in settings['channels']:
                    self.logger.warning(f' * Found message forwarding channel #{channel.name} in guild "{guild.name}"!')
                    settings['channels'][channel.id] = {
                        'channel': channel,
                        'forward': settings['channels'][channel.id],
                        'webhook': {},
                        'message': {}
                    }

                if settings['gamelist'] and channel.id == settings['gamelist']:
                    self.logger.warning(f' * Found game list channel #{channel.name} in guild "{guild.name}"!')
                    settings['gamelist'] = {
                        'channel': channel,
                        'message': {}
                    }

            for id, values in settings['channels'].items():
                if type(values) != dict:
                    raise Exception(f'Could not find the message forwarding channel with id {id} in guild "{guild.name}".')

            if settings['gamelist'] and type(settings['gamelist']) != dict:
                raise Exception(f'Could not find the game list channel with id {settings["gamelist"]} in guild "{guild.name}".')

    # looks for the first pinned message from this bot to use as a game list
    async def check_gamelists(self):
        for settings in self.guild_list.values():
            if settings['gamelist']:
                guild = settings['guild']
                channel = settings['gamelist']['channel']

                for message in await channel.pins():
                    if message.author == self.user:
                        settings['gamelist']['message'] = message

                if not isinstance(settings['gamelist']['message'], discord.Message):
                    self.logger.warning(f' ! No pinned game list belonging to "{self.user.name}" in #{channel.name} on "{guild.name}".')
                    settings['gamelist']['message'] = await channel.send(self.bot_message_setup)
                    await settings['gamelist']['message'].pin()
                    self.logger.warning(f' * Created and pinned game list in #{channel.name} on "{guild.name}".')
                else:
                    self.logger.warning(f' * Found pinned game list in #{channel.name} on "{guild.name}"!')

    # make sure webhooks exist in all forwarding channels
    async def check_webhooks(self):
        for settings in self.guild_list.values():
            guild = settings['guild']
            for channel_settings in settings['channels'].values():
                channel = channel_settings['channel']

                for webhook in await channel.webhooks():
                    if webhook.name == self.user.name:
                        channel_settings['webhook'] = webhook
                        self.logger.warning(f' * Found webhook with name {self.user.name} in #{channel.name} on "{guild.name}"!')
                        break

                if not channel_settings['webhook']:
                    self.logger.warning(f' ! Could not find webhook with name {self.user.name} in #{channel.name} on "{guild.name}"!')
                    channel_settings['webhook'] = await channel.create_webhook(name=self.user.name,
                                                                               avatar=await self.user.avatar_url.read())
                    self.logger.warning(f' * Created webhook with name {self.user.name} in #{channel.name} on "{guild.name}"!')

    async def check_userlists(self):
        for settings in self.guild_list.values():
            guild = settings['guild']
            for channel_settings in settings['channels'].values():
                channel = channel_settings['channel']

                if settings['gamelist']['channel'] == channel:
                    raise Exception(f'Can\'t have both user list and game list in #{channel.name} on "{guild.name}", check configuration.')

                for message in await channel.pins():
                    if message.author == self.user:
                        channel_settings['message'] = message

                if not isinstance(channel_settings['message'], discord.Message):
                    self.logger.warning(f' ! No pinned user list belonging to "{self.user.name}" in #{channel.name} on "{guild.name}".')
                    channel_settings['message'] = await channel.send(self.bot_message_setup)
                    await channel_settings['message'].pin()
                    self.logger.warning(f' * Created and pinned user list in #{channel.name} on "{guild.name}".')
                else:
                    self.logger.warning(f' * Found pinned user list in #{channel.name} on "{guild.name}"!')

    # edits pinned message containing game lists
    async def update_gamelists(self, **kwargs):

        # not safe to interact with discord before initialization is complete
        if not self.prepared:
            return self.logger.warning(' ! Attempted to forward message to Discord before initialization was fully complete.')

        for settings in self.guild_list.values():
            guild = settings['guild']
            if settings['gamelist']:
                channel = settings['gamelist']['channel']
                await settings['gamelist']['message'].edit(**kwargs)
                self.logger.warning(f' * Updated pinned game list in #{channel.name} on "{guild.name}".')

    # edits pinned messages containing user list for given channel
    async def update_userlists(self, channels: dict, interval=7):
        while True:
            await asyncio.sleep(interval)

            # not safe to interact with discord before initialization is complete
            if not self.prepared:
                return self.logger.warning(' ! Attempted to update userlist on Discord before initialization was fully complete.')

            for channel, users in channels.items():
                userlist = discord.Embed(colour=self.embed_color, timestamp=datetime.now(timezone.utc))
                userlist.set_footer(text=self.embed_footer, icon_url=self.embed_icon)

                if not users or not len(users):
                    userlist.description = self.embed_no_users
                else:
                    users = sorted(users, key=str.lower)
                    field = ''
                    title = f'{len(users)} users online in #{channel}'
                    for user in users:
                        append = discord.utils.escape_markdown(user) + '\n'

                        if len(field) + len(append) >= 1024:
                            userlist.add_field(name=title, value=field, inline=False)
                            field = ''
                        field += append

                    if len(field) <= 0:
                        userlist.description = self.embed_no_host
                    else:
                        userlist.add_field(
                            name=title, value=field, inline=False)

                for settings in self.guild_list.values():
                    for channel_settings in settings['channels'].values():
                        if channel == channel_settings['forward']:
                            await channel_settings['message'].edit(content=None, embed=userlist)

    # forwards messages to channel using webhooks, if nickname exist on discord it will use their avatar
    async def send_message(self, irc_channel: str, sender: str, message: str, action: bool = False, snooper: str = None,
                           origin: discord.TextChannel = None):

        # not safe to interact with discord before initialization is complete
        if not self.prepared:
            return self.logger.warning(' ! Attempted to forward message to Discord before initialization was fully complete.')

        # ignore blank lines, since discord won't let me post a message with only whitespaces.
        if not message or message.isspace():
            return self.logger.warning(f' * Ignoring blank WormNET message from {sender} in #{irc_channel}.')

        # strip links due to spam
        message = discord.utils.escape_markdown(message)
        message = re.sub(r'(https?://\S+)', r'<\g<1>>', message, flags=re.MULTILINE)

        # replace regular @ (COMMERCIAL AT) with ＠ (FULLWIDTH COMMERCIAL AT) homoglyph due to ADOLF-HITLER
        message = message.replace('@', '\N{FULLWIDTH COMMERCIAL AT}')

        # actions need to be italics
        if action:
            message = '*' + message + '*'

        # loop through every saved guild and every saved channel and forward message only to specific channels
        for settings in self.guild_list.values():
            guild = settings['guild']
            for channel_settings in settings['channels'].values():
                channel = channel_settings['channel']
                if channel_settings['forward'] == irc_channel and origin != channel_settings['channel']:

                    # log type of message, and message contents
                    if origin:
                        self.logger.warning(f' * Forwarding message by {sender} on Discord #{origin.name} on'
                                            f' "{origin.guild.name}" to #{channel.name} on "{guild.name}": {message}')
                    else:
                        self.logger.warning(f' * Forwarding message by {sender} on WormNET #{irc_channel} to '
                                            f'#{channel.name} on "{guild.name}": {message}')

                    # then proceed to find user avatar if possible, and post it using the webhook
                    member = guild.get_member_named(sender)
                    username = sender if not snooper else f'{sender} ({snooper})'
                    avatar_url = member.avatar_url if isinstance(member, discord.Member) else None
                    await channel_settings['webhook'].send(content=message, username=username, avatar_url=avatar_url)

    # find forwarding channel name as string
    async def find_forward_channel(self, channel: discord.TextChannel):
        for guild_name, guild_info in self.guild_list.items():
            for channel_name, channel_info in guild_info['channels'].items():
                if channel_info['channel'] == channel and guild_info['guild'] == channel.guild:
                    return channel_info['forward']
        return False

    # EVENT LISTENERS #

    async def on_message(self, message):
        if message.author == self.user or not len(message.clean_content) or message.webhook_id:
            return

        # forward to all other discord servers
        irc_channel = await self.find_forward_channel(channel=message.channel)
        sender = message.author.name
        snooper = 'Other Discord'
        await self.send_message(irc_channel=irc_channel, sender=sender, message=message.content, snooper=snooper,
                                origin=message.channel)

        # finally forward to IRC
        for settings in self.guild_list.values():
            guild = settings['guild']
            for channel_settings in settings['channels'].values():
                channel = channel_settings['channel']
                if channel == message.channel and guild == message.guild:
                    await self.forward_message(
                        guild=guild.name,
                        origin=channel.name,
                        channel=channel_settings['forward'],
                        message=f'{message.author.display_name}> {message.clean_content}'
                    )

    # catch all errors and propagate this error to the loop exception handler
    async def on_error(self, event):
        raise

    async def on_ready(self):
        await self.check_guilds()
        await self.check_channels()
        await self.check_gamelists()
        await self.check_userlists()
        await self.check_webhooks()
        self.prepared = True
        self.logger.warning(f' * {self.user.name} has been fully initialized!')

    async def on_disconnect(self):
        self.logger.warning(f' * {self.user.name} has disconnected from Discord!')

    async def on_connect(self):
        self.logger.warning(f' * {self.user.name} has connected to Discord!')
