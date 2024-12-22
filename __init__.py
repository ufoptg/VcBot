# Ultroid - UserBot
# Copyright (C) 2021-2022 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

# ----------------------------------------------------------#
#                                                           #
#    _   _ _   _____ ____   ___ ___ ____   __     ______    #
#   | | | | | |_   _|  _ \ / _ \_ _|  _ \  \ \   / / ___|   #
#   | | | | |   | | | |_) | | | | || | | |  \ \ / / |       #
#   | |_| | |___| | |  _ <| |_| | || |_| |   \ V /| |___    #
#    \___/|_____|_| |_| \_\\___/___|____/     \_/  \____|   #
#                                                           #
# ----------------------------------------------------------#


import asyncio
import os
from time import time
from typing import Any, Dict, List, Optional, Tuple

from pytgcalls import GroupCallFactory
from pytgcalls.exceptions import GroupCallNotFoundError
from telethon import events, functions
from telethon.errors.rpcerrorlist import (
    ParticipantJoinMissingError,
    ChatSendMediaForbiddenError,
)
from telethon.utils import get_display_name
from yt_dlp import YoutubeDL
from youtubesearchpython import VideosSearch

from pyUltroid import HNDLR, LOGS, asst, udB, vcClient
from pyUltroid._misc._decorators import compile_pattern
from pyUltroid.fns.helper import (
    bash,
    downloader,
    inline_mention,
    mediainfo,
    time_formatter,
)
from pyUltroid.fns.admins import admin_check
from pyUltroid.fns.tools import is_url_ok
from pyUltroid.fns.ytdl import get_videos_link
from pyUltroid._misc import owner_and_sudos
from pyUltroid._misc._wrappers import eod, eor
from pyUltroid.version import __version__ as UltVer

from strings import get_string

# Constants
DEFAULT_THUMBNAIL = "https://telegra.ph/file/22bb2349da20c7524e4db.mp4"

# Types
QueueType = Dict[int, Dict[str, Any]]

# Initialize Global Variables
LOG_CHANNEL = udB.get_key("LOG_CHANNEL")
ACTIVE_CALLS: List[int] = []
VC_QUEUE: Dict[int, QueueType] = {}
MSGID_CACHE: Dict[int, Any] = {}
VIDEO_ON: Dict[int, Any] = {}
CLIENTS: Dict[int, Any] = {}


def get_vc_auth_users() -> List[int]:
    """
    Retrieves a list of user IDs authorized for voice chat actions.
    Includes owners, sudoers, and additional VC sudoers.
    """
    vc_sudos = udB.get_key("VC_SUDOS") or []
    return [int(user_id) for user_id in (*owner_and_sudos(), *vc_sudos)]


class VoiceChatManager:
    """
    Manages voice chat operations including joining, leaving, and handling playback queues.
    """

    def __init__(self, chat_id: int, event: Optional[events.NewMessage.Event] = None, video: bool = False):
        self.chat_id = chat_id
        self.current_chat = event.chat_id if event else LOG_CHANNEL
        self.video = video
        self.group_call = self._get_or_create_group_call()

    def _get_or_create_group_call(self):
        if CLIENTS.get(self.chat_id):
            return CLIENTS[self.chat_id]
        else:
            client = GroupCallFactory(
                vcClient, GroupCallFactory.MTPROTO_CLIENT_TYPE.TELETHON,
            )
            group_call = client.get_group_call()
            CLIENTS[self.chat_id] = group_call
            return group_call

    async def make_vc_active(self) -> Tuple[bool, Optional[Exception]]:
        try:
            await vcClient(
                functions.phone.CreateGroupCallRequest(
                    self.chat_id, title="🎧 Ultroid Music 🎶"
                )
            )
        except Exception as e:
            LOGS.exception(e)
            return False, e
        return True, None

    async def start_call(self) -> Tuple[bool, Optional[Exception]]:
        if VIDEO_ON:
            for chats in list(VIDEO_ON):
                await VIDEO_ON[chats].stop()
            VIDEO_ON.clear()
            await asyncio.sleep(3)

        if self.video:
            for chat in list(CLIENTS):
                if chat != self.chat_id:
                    await CLIENTS[chat].stop()
                    del CLIENTS[chat]
            VIDEO_ON[self.chat_id] = self.group_call

        if self.chat_id not in ACTIVE_CALLS:
            try:
                self.group_call.on_network_status_changed(self.on_network_changed)
                self.group_call.on_playout_ended(self.playout_ended_handler)
                await self.group_call.join(self.chat_id)
            except GroupCallNotFoundError as er:
                LOGS.info(er)
                done, err = await self.make_vc_active()
                if not done:
                    return False, err
            except Exception as e:
                LOGS.exception(e)
                return False, e
            ACTIVE_CALLS.append(self.chat_id)
        return True, None

    async def on_network_changed(self, call, is_connected):
        if is_connected and self.chat_id not in ACTIVE_CALLS:
            ACTIVE_CALLS.append(self.chat_id)
        elif not is_connected and self.chat_id in ACTIVE_CALLS:
            ACTIVE_CALLS.remove(self.chat_id)

    async def playout_ended_handler(self, call, source, mtype):
        if os.path.exists(source):
            os.remove(source)
        await self.play_from_queue()

    async def play_from_queue(self):
        chat_id = self.chat_id
        if chat_id in VIDEO_ON:
            await self.group_call.stop_video()
            VIDEO_ON.pop(chat_id, None)

        try:
            song_info = await VoiceChatManager.get_from_queue(chat_id)
            if not song_info:
                raise ValueError("No songs in queue.")

            song, title, link, thumb, from_user, pos, dur = song_info

            try:
                await self.group_call.start_audio(song)
            except ParticipantJoinMissingError:
                if not await self.ensure_vc_join():
                    return
                await self.group_call.start_audio(song)

            # Delete previous message if exists
            if MSGID_CACHE.get(chat_id):
                await MSGID_CACHE[chat_id].delete()
                MSGID_CACHE.pop(chat_id, None)

            message_text = (
                f"<strong>🎧 Now playing #{pos}: <a href='{link}'>{title}</a></strong>\n"
                f"⏰ Duration: <code>{dur}</code>\n"
                f"👤 <strong>Requested by:</strong> {from_user}"
            )

            try:
                message = await vcClient.send_message(
                    self.current_chat,
                    message_text,
                    file=thumb if thumb else DEFAULT_THUMBNAIL,
                    link_preview=False,
                    parse_mode="html",
                )
            except ChatSendMediaForbiddenError:
                message = await vcClient.send_message(
                    self.current_chat, message_text, link_preview=False, parse_mode="html"
                )

            MSGID_CACHE[chat_id] = message
            VC_QUEUE[chat_id].pop(pos, None)
            if not VC_QUEUE[chat_id]:
                VC_QUEUE.pop(chat_id, None)

        except (IndexError, KeyError, ValueError):
            await self.group_call.stop()
            CLIENTS.pop(self.chat_id, None)
            ACTIVE_CALLS.remove(self.chat_id)
            await vcClient.send_message(
                self.current_chat,
                f"• Successfully left VC: <code>{chat_id}</code> •",
                parse_mode="html",
            )
        except Exception as er:
            LOGS.exception(er)
            await vcClient.send_message(
                self.current_chat,
                f"<strong>ERROR:</strong> An unexpected error occurred.",
                parse_mode="html",
            )

    async def ensure_vc_join(self) -> bool:
        done, err = await self.start_call()
        if done:
            await vcClient.send_message(
                self.current_chat,
                f"• Joined VC in <code>{self.chat_id}</code>",
                parse_mode="html",
            )
            return True
        else:
            await vcClient.send_message(
                self.current_chat,
                f"<strong>ERROR while Joining VC -</strong> <code>{self.chat_id}</code> :\n<code>{str(err)}</code>",
                parse_mode="html",
            )
            return False

    @staticmethod
    async def get_from_queue(chat_id: int) -> Optional[Tuple[Any, str, str, str, str, int, str]]:
        queue = VC_QUEUE.get(chat_id)
        if not queue:
            return None
        play_position = min(queue.keys())
        info = queue[play_position]
        song = info.get("song") or await VoiceChatManager.fetch_stream_link(info["link"])
        return (
            song,
            info["title"],
            info["link"],
            info["thumb"],
            info["from_user"],
            play_position,
            info["duration"],
        )

    @staticmethod
    async def fetch_stream_link(yt_link: str) -> Optional[str]:
        """
        Fetches the best stream link for a given YouTube URL using yt-dlp.
        """
        if not YoutubeDL:
            LOGS.error("'yt-dlp' is not installed.")
            return None
        ydl_opts = {
            'format': 'best[height<=720][width<=1280]',
            'quiet': True,
            'no_warnings': True,
        }
        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(yt_link, download=False)
                formats = info.get('formats', [])
                for fmt in formats:
                    if fmt.get('height') and fmt.get('width'):
                        if fmt['height'] <= 720 and fmt['width'] <= 1280:
                            return fmt['url']
        except Exception as e:
            LOGS.exception(f"Failed to fetch stream link: {e}")
        return None


def vc_asst(dec: str, **kwargs):
    """
    Decorator for handling voice chat-related commands with proper authorization.
    """
    def decorator(func):
        kwargs["func"] = (
            lambda e: not e.is_private and not e.via_bot_id and not e.fwd_from
        )
        handler = udB.get_key("VC_HNDLR") or HNDLR
        kwargs["pattern"] = compile_pattern(dec, handler)
        vc_auth = kwargs.pop("vc_auth", True)
        auth_groups = udB.get_key("VC_AUTH_GROUPS") or {}

        async def vc_handler(event: events.NewMessage.Event):
            VCAUTH_GROUPS = list(auth_groups.keys())
            user_id = event.sender_id
            chat_id = event.chat_id

            if not (
                event.out
                or user_id in get_vc_auth_users()
                or (vc_auth and chat_id in VCAUTH_GROUPS)
            ):
                return

            if vc_auth and auth_groups.get(chat_id):
                admin_required = auth_groups[chat_id].get("admins", False)
                if admin_required and not await admin_check(event):
                    return

            try:
                await func(event)
            except Exception as e:
                LOGS.exception(e)
                await asst.send_message(
                    LOG_CHANNEL,
                    f"VC Error - <code>{UltVer}</code>\n\n"
                    f"<code>{event.text}</code>\n\n"
                    f"<code>{str(e)}</code>",
                    parse_mode="html",
                )

        vcClient.add_event_handler(vc_handler, events.NewMessage(**kwargs))
        return decorator

    return decorator


def add_to_queue(
    chat_id: int,
    song: Optional[Any],
    song_name: str,
    link: str,
    thumb: str,
    from_user: str,
    duration: str
) -> Dict[int, Any]:
    """
    Adds a song to the queue for a specific chat.
    """
    play_position = max(VC_QUEUE.get(chat_id, {}).keys(), default=0) + 1
    VC_QUEUE.setdefault(chat_id, {})[play_position] = {
        "song": song,
        "title": song_name,
        "link": link,
        "thumb": thumb,
        "from_user": from_user,
        "duration": duration,
    }
    return VC_QUEUE[chat_id]


def list_queue(chat_id: int) -> str:
    """
    Generates a formatted string listing the queue for a specific chat.
    """
    if not VC_QUEUE.get(chat_id):
        return "The queue is currently empty."

    lines = []
    for idx, key in enumerate(sorted(VC_QUEUE[chat_id].keys())[:18], start=1):
        song = VC_QUEUE[chat_id][key]
        lines.append(
            f"<strong>{idx}. <a href='{song['link']}'>{song['title']}</a> :</strong> <i>By: {song['from_user']}</i>"
        )
    lines.append("\n\n.....")
    return "\n".join(lines)


async def download(query: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str], str]:
    """
    Downloads a song based on a query which can be a URL or a search term.
    Returns download link, thumbnail URL, title, original link, and duration.
    """
    if query.startswith("https://") and "youtube" not in query.lower():
        return None, None, query, query, "Unknown"

    if not VideosSearch:
        LOGS.error("'youtubesearchpython' is not installed.")
        return None, None, None, None, "Unknown"

    search = VideosSearch(query, limit=1).result()
    if not search["result"]:
        LOGS.error(f"No results found for query: {query}")
        return None, None, None, None, "Unknown"

    data = search["result"][0]
    link = data["link"]
    title = data["title"]
    duration = data.get("duration") or "♾"
    thumb = f"https://i.ytimg.com/vi/{data['id']}/hqdefault.jpg"

    stream_link = await VoiceChatManager.fetch_stream_link(link)
    return stream_link, thumb, title, link, duration


async def vid_download(query: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str], str]:
    """
    Downloads a video based on a search query.
    Returns video link, thumbnail URL, title, original link, and duration.
    """
    if not VideosSearch:
        LOGS.error("'youtubesearchpython' is not installed.")
        return None, None, None, None, "Unknown"

    search = VideosSearch(query, limit=1).result()
    if not search["result"]:
        LOGS.error(f"No video results found for query: {query}")
        return None, None, None, None, "Unknown"

    data = search["result"][0]
    link = data["link"]
    title = data["title"]
    duration = data.get("duration") or "♾"
    thumb = f"https://i.ytimg.com/vi/{data['id']}/hqdefault.jpg"

    video_link = await VoiceChatManager.fetch_stream_link(link)
    return video_link, thumb, title, link, duration


async def dl_playlist(chat_id: int, from_user: str, link: str):
    """
    Downloads a playlist from a given link and adds all songs to the queue.
    """
    links = await get_videos_link(link)
    if not links:
        LOGS.error(f"No videos found in playlist: {link}")
        return

    for video_link in links:
        try:
            search = VideosSearch(video_link, limit=1).result()
            if not search["result"]:
                LOGS.error(f"No results found for video link: {video_link}")
                continue

            vid = search["result"][0]
            duration = vid.get("duration") or "♾"
            title = vid["title"]
            thumb = f"https://i.ytimg.com/vi/{vid['id']}/hqdefault.jpg"

            stream_link = await VoiceChatManager.fetch_stream_link(vid["link"])
            add_to_queue(chat_id, None, title, vid["link"], thumb, from_user, duration)
        except Exception as e:
            LOGS.exception(f"Failed to add video to queue: {e}")


async def file_download(event: events.NewMessage.Event, reply, fast_download: bool = True) -> Tuple[Optional[str], str, str, str, str]:
    """
    Downloads a media file from a replied message.
    Returns file path, thumbnail path, title, message link, and duration.
    """
    thumb = DEFAULT_THUMBNAIL
    title = reply.file.title or reply.file.name or f"{int(time())}.mp4"
    file_name = reply.file.name or f"{int(time())}.mp4"
    file_path = f"vcbot/downloads/{file_name}"

    try:
        if fast_download:
            downloaded_file = await downloader(
                file_path,
                reply.media.document,
                event,
                int(time()),
                f"Downloading {title}...",
            )
            file_path = downloaded_file.name
        else:
            downloaded_file = await reply.download_media(file_path)

        duration = (
            time_formatter(reply.file.duration * 1000) if reply.file.duration else "🤷‍♂️"
        )

        if reply.document and reply.document.thumbs:
            thumb_path = await reply.download_media("vcbot/downloads/", thumb=-1)
        else:
            thumb_path = thumb

        return file_path, thumb_path, title, reply.message.link, duration
    except Exception as e:
        LOGS.exception(f"Failed to download file: {e}")
        return None, thumb, title, reply.message.link, "Unknown"
