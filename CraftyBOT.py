import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands


def load_local_env() -> None:
    env_path = Path(__file__).with_name('.env')
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_local_env()
TOKEN = os.getenv("DISCORD_BOT_TOKEN")

PC_CHANNEL_ID = 1472031752213233707
MOBILE_CHANNEL_ID = 1472031348926582814
ADMIN_LOG_CHANNEL_ID = 1472231359203246284
ADMIN_ROLE_ID = 1450957069938327813
MAX_WARNINGS = 3

PC_COLOR = 0x3498DB
MOBILE_COLOR = 0xE67E22
REVIEW_COLOR = 0xF1C40F

PLATFORM_CHANNELS = {
    "PC Edition": PC_CHANNEL_ID,
    "Mobile Edition": MOBILE_CHANNEL_ID,
}

PLATFORM_COLORS = {
    "PC Edition": PC_COLOR,
    "Mobile Edition": MOBILE_COLOR,
}

warnings: dict[int, dict[int, int]] = {}


@dataclass(slots=True)
class Submission:
    name: str
    description: str
    platform: str
    attachments: list[str]
    author_id: int
    author_name: str
    author_avatar_url: str


class Crafty(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True
        intents.guilds = True
        super().__init__(command_prefix="!", intents=intents)
        self.channel_cache: dict[int, discord.abc.GuildChannel] = {}

    async def setup_hook(self) -> None:
        self.add_view(ReviewView())
        await self.tree.sync()
        print("Slash commands synced.")

    async def on_ready(self) -> None:
        print(f"Logged in as {self.user} ({self.user.id})")

    def get_cached_channel(
        self,
        guild: discord.Guild,
        channel_id: int,
    ) -> discord.abc.GuildChannel | None:
        channel = self.channel_cache.get(channel_id)
        if channel is None:
            channel = guild.get_channel(channel_id)
            if channel is not None:
                self.channel_cache[channel_id] = channel
        return channel


bot = Crafty()


def is_admin(member: discord.Member) -> bool:
    role = member.guild.get_role(ADMIN_ROLE_ID)
    return bool(role and role in member.roles)


async def ensure_admin(interaction: discord.Interaction) -> bool:
    if not isinstance(interaction.user, discord.Member) or not is_admin(interaction.user):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return False
    return True


def get_warning_count(guild_id: int, member_id: int) -> int:
    return warnings.get(guild_id, {}).get(member_id, 0)


def add_warning(guild_id: int, member_id: int) -> int:
    guild_warnings = warnings.setdefault(guild_id, {})
    guild_warnings[member_id] = guild_warnings.get(member_id, 0) + 1
    return guild_warnings[member_id]


def clear_warnings(guild_id: int, member_id: int) -> None:
    guild_warnings = warnings.get(guild_id)
    if not guild_warnings:
        return
    guild_warnings.pop(member_id, None)
    if not guild_warnings:
        warnings.pop(guild_id, None)


def build_public_embed(submission: Submission) -> discord.Embed:
    embed = discord.Embed(
        title=submission.name,
        description=f"```{submission.description}```",
        color=PLATFORM_COLORS[submission.platform],
        timestamp=discord.utils.utcnow(),
    )
    embed.set_author(name=submission.author_name, icon_url=submission.author_avatar_url)
    embed.set_footer(text=f"Crafty | {submission.platform}")
    if submission.attachments:
        embed.set_image(url=submission.attachments[0])
    return embed


class ReviewView(discord.ui.View):
    def __init__(self, submission: Submission | None = None) -> None:
        super().__init__(timeout=None)
        self.submission = submission

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, custom_id="review:approve")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        if not await ensure_admin(interaction):
            return

        if self.submission is None or interaction.guild is None:
            await interaction.response.send_message("Submission data is unavailable.", ephemeral=True)
            return

        target_channel = bot.get_cached_channel(
            interaction.guild,
            PLATFORM_CHANNELS[self.submission.platform],
        )
        if not isinstance(target_channel, discord.TextChannel):
            await interaction.response.send_message("Public channel not found.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        await target_channel.send(embed=build_public_embed(self.submission))

        for file_url in self.submission.attachments[1:]:
            await target_channel.send(file_url)

        if interaction.message:
            await interaction.message.delete()
        await interaction.followup.send("Approved and published.", ephemeral=True)

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger, custom_id="review:reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        if not await ensure_admin(interaction):
            return

        if self.submission is None:
            await interaction.response.send_message("Submission data is unavailable.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        user = interaction.client.get_user(self.submission.author_id)
        if user is None:
            user = await interaction.client.fetch_user(self.submission.author_id)

        try:
            await user.send(f"Your script **{self.submission.name}** was rejected.")
        except discord.HTTPException:
            pass

        if interaction.message:
            await interaction.message.delete()
        await interaction.followup.send("Script rejected.", ephemeral=True)


class ScriptModal(discord.ui.Modal, title="Submit Your Script"):
    name = discord.ui.TextInput(label="Script Title", max_length=100)
    description = discord.ui.TextInput(
        label="Script Description",
        style=discord.TextStyle.long,
        max_length=4000,
    )

    def __init__(self, platform: str, attachments: list[str]) -> None:
        super().__init__()
        self.platform = platform
        self.attachments = attachments

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        admin_channel = bot.get_cached_channel(interaction.guild, ADMIN_LOG_CHANNEL_ID)
        if not isinstance(admin_channel, discord.TextChannel):
            await interaction.response.send_message(
                "Admin channel not found. Check ADMIN_LOG_CHANNEL_ID.",
                ephemeral=True,
            )
            return

        submission = Submission(
            name=self.name.value.strip(),
            description=self.description.value.strip(),
            platform=self.platform,
            attachments=self.attachments,
            author_id=interaction.user.id,
            author_name=interaction.user.display_name,
            author_avatar_url=interaction.user.display_avatar.url,
        )

        embed = discord.Embed(
            title="New Script Submission (Pending)",
            color=REVIEW_COLOR,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Platform", value=self.platform, inline=True)
        embed.add_field(name="Title", value=submission.name, inline=True)
        embed.add_field(name="Author", value=interaction.user.mention, inline=False)
        embed.add_field(name="Description", value=f"```{submission.description}```", inline=False)

        await admin_channel.send(embed=embed, view=ReviewView(submission))
        await interaction.response.send_message("Your script has been submitted for review.", ephemeral=True)


@bot.tree.command(name="script", description="Submit a script")
@app_commands.describe(
    platform="Choose platform",
    attachment1="Optional file",
    attachment2="Optional file",
    attachment3="Optional file",
)
@app_commands.choices(
    platform=[
        app_commands.Choice(name="PC Edition", value="PC Edition"),
        app_commands.Choice(name="Mobile Edition", value="Mobile Edition"),
    ]
)
async def script(
    interaction: discord.Interaction,
    platform: app_commands.Choice[str],
    attachment1: discord.Attachment | None = None,
    attachment2: discord.Attachment | None = None,
    attachment3: discord.Attachment | None = None,
) -> None:
    attachments = [attachment.url for attachment in (attachment1, attachment2, attachment3) if attachment]
    await interaction.response.send_modal(ScriptModal(platform.value, attachments))


@bot.tree.command(name="ping", description="Check bot latency")
async def ping(interaction: discord.Interaction) -> None:
    latency_ms = round(bot.latency * 1000)
    await interaction.response.send_message(f"Pong! {latency_ms} ms", ephemeral=True)


@bot.tree.command(name="serverinfo", description="Show a quick server summary")
async def serverinfo(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("This command only works in a server.", ephemeral=True)
        return

    embed = discord.Embed(
        title=guild.name,
        color=PC_COLOR,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Members", value=str(guild.member_count or 0), inline=True)
    embed.add_field(name="Channels", value=str(len(guild.channels)), inline=True)
    embed.add_field(name="Roles", value=str(len(guild.roles)), inline=True)
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="say", description="Send a message as the bot")
@app_commands.describe(message="Message to send", channel="Optional target channel")
async def say(
    interaction: discord.Interaction,
    message: str,
    channel: discord.TextChannel | None = None,
) -> None:
    if not await ensure_admin(interaction):
        return

    target_channel = channel or interaction.channel
    if not isinstance(target_channel, discord.TextChannel):
        await interaction.response.send_message("This channel does not support messages.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    await target_channel.send(message)
    await interaction.followup.send(f"Sent message to {target_channel.mention}.", ephemeral=True)


@bot.tree.command(name="clear", description="Clear messages")
@app_commands.describe(amount="Number of recent messages to remove")
async def clear(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100]) -> None:
    if not await ensure_admin(interaction):
        return

    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("This command only works in text channels.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.followup.send(f"Deleted {len(deleted)} messages.", ephemeral=True)


@bot.tree.command(name="kick", description="Kick a member")
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str | None = None) -> None:
    if not await ensure_admin(interaction):
        return

    await member.kick(reason=reason)
    clear_warnings(interaction.guild.id, member.id)
    await interaction.response.send_message(f"{member.mention} kicked.")


@bot.tree.command(name="warn", description="Warn a member. Ban automatically at 3 warnings")
@app_commands.describe(member="Member to warn", reason="Why they are being warned")
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str | None = None) -> None:
    if not await ensure_admin(interaction):
        return

    if interaction.guild is None:
        await interaction.response.send_message("This command only works in a server.", ephemeral=True)
        return

    if member.id == interaction.user.id:
        await interaction.response.send_message("You cannot warn yourself.", ephemeral=True)
        return

    count = add_warning(interaction.guild.id, member.id)
    reason_text = reason or "No reason provided."

    try:
        await member.send(
            f"You were warned in **{interaction.guild.name}**. Warning {count}/{MAX_WARNINGS}. Reason: {reason_text}"
        )
    except discord.HTTPException:
        pass

    if count >= MAX_WARNINGS:
        await member.ban(reason=f"Reached {MAX_WARNINGS} warnings. {reason_text}")
        clear_warnings(interaction.guild.id, member.id)
        await interaction.response.send_message(
            f"{member.mention} warned ({count}/{MAX_WARNINGS}) and banned.")
        return

    await interaction.response.send_message(
        f"{member.mention} warned ({count}/{MAX_WARNINGS}). Reason: {reason_text}")


@bot.tree.command(name="warnings", description="Show a member's warning count")
async def warnings_command(interaction: discord.Interaction, member: discord.Member) -> None:
    if not await ensure_admin(interaction):
        return

    if interaction.guild is None:
        await interaction.response.send_message("This command only works in a server.", ephemeral=True)
        return

    count = get_warning_count(interaction.guild.id, member.id)
    await interaction.response.send_message(f"{member.mention} has {count}/{MAX_WARNINGS} warnings.")


@bot.tree.command(name="clearwarnings", description="Clear a member's warnings")
async def clearwarnings(interaction: discord.Interaction, member: discord.Member) -> None:
    if not await ensure_admin(interaction):
        return

    if interaction.guild is None:
        await interaction.response.send_message("This command only works in a server.", ephemeral=True)
        return

    clear_warnings(interaction.guild.id, member.id)
    await interaction.response.send_message(f"Cleared warnings for {member.mention}.")


@bot.tree.command(name="ban", description="Ban a member")
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str | None = None) -> None:
    if not await ensure_admin(interaction):
        return

    await member.ban(reason=reason)
    clear_warnings(interaction.guild.id, member.id)
    await interaction.response.send_message(f"{member.mention} banned.")


@bot.tree.command(name="unban", description="Unban a user by ID")
@app_commands.describe(user_id="The Discord user ID to unban", reason="Optional reason")
async def unban(interaction: discord.Interaction, user_id: str, reason: str | None = None) -> None:
    if not await ensure_admin(interaction):
        return

    if interaction.guild is None:
        await interaction.response.send_message("This command only works in a server.", ephemeral=True)
        return

    try:
        user = await bot.fetch_user(int(user_id))
    except (ValueError, discord.NotFound):
        await interaction.response.send_message("Invalid user ID.", ephemeral=True)
        return

    await interaction.guild.unban(user, reason=reason)
    await interaction.response.send_message(f"{user} unbanned.")


@bot.tree.command(name="mute", description="Timeout a member")
async def mute(
    interaction: discord.Interaction,
    member: discord.Member,
    minutes: app_commands.Range[int, 1, 10080],
    reason: str | None = None,
) -> None:
    if not await ensure_admin(interaction):
        return

    until = discord.utils.utcnow() + timedelta(minutes=minutes)
    await member.timeout(until, reason=reason)
    await interaction.response.send_message(f"{member.mention} muted for {minutes} minutes.")


@bot.tree.command(name="unmute", description="Remove a member timeout")
async def unmute(interaction: discord.Interaction, member: discord.Member, reason: str | None = None) -> None:
    if not await ensure_admin(interaction):
        return

    await member.timeout(None, reason=reason)
    await interaction.response.send_message(f"{member.mention} unmuted.")


if not TOKEN:
    raise RuntimeError("Missing DISCORD_BOT_TOKEN environment variable.")

bot.run(TOKEN)
