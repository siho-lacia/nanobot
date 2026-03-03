"""Zulip channel implementation using zulip SDK."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from loguru import logger

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.schema import ZulipConfig

try:
    import zulip
except ImportError:
    zulip = None  # type: ignore


def _markdown_to_zulip(text: str) -> str:
    """Convert markdown to Zulip-compatible format."""
    if not text:
        return ""

    # Zulip supports most markdown natively, but we need to handle some edge cases

    # Convert [text](url) to Zulip's link format (same as markdown)
    # Already compatible

    # Convert headers - Zulip supports ** for bold, * for italic
    # Headers with # are also supported

    return text


def _zulip_to_markdown(content: str) -> str:
    """Convert Zulip content to markdown."""
    if not content:
        return ""

    # Zulip uses @**username** for user mentions, convert to @username
    content = re.sub(r"@\*\*([^*|]+)(\|\d+)?\*\*", r"@\1", content)

    # Zulip uses @_**user|id** for silent mentions (with underscore)
    content = re.sub(r"@_\*\*[^|]+\|\d+\*\*", "@user", content)

    # Zulip uses #**stream/topic** for stream/topic links
    content = re.sub(r"#\*\*([^*]+)\*\*", r"#\1", content)

    # Zulip quote format: [said](url): ````quote\n...\n```
    # Convert to markdown blockquote format
    def convert_quote(match: re.Match) -> str:
        quoted_content = match.group(1)
        # Convert each line to blockquote
        lines = quoted_content.strip().split("\n")
        return "\n".join(f"> {line}" for line in lines)

    content = re.sub(
        r"\[said\]\([^)]+\):\s*````quote\n([\s\S]*?)\n````",
        convert_quote,
        content,
    )

    # Remove reply header lines like "@Sender Name:" that precede quotes
    # These are added by Zulip when replying to messages
    content = re.sub(r"^@[^:]+:\s*\n", "", content, flags=re.MULTILINE)

    # Clean up empty blockquote lines
    content = re.sub(r"> \s*$", "", content, flags=re.MULTILINE)

    return content.strip()


class ZulipChannel(BaseChannel):
    """Zulip channel using the zulip SDK."""

    name = "zulip"

    def __init__(self, config: ZulipConfig, bus: MessageBus):
        super().__init__(config, bus)
        self.config: ZulipConfig = config
        self._client: Any = None
        self._event_queue: asyncio.Queue[dict] = asyncio.Queue()
        self._event_task: asyncio.Task | None = None
        self._queue_name: str = ""

    async def start(self) -> None:
        """Start the Zulip client."""
        if zulip is None:
            logger.error("zulip package not installed. Run: pip install zulip")
            return

        if not self.config.site or not self.config.bot_email or not self.config.api_key:
            logger.error("Zulip configuration incomplete: site, bot_email, and api_key required")
            return

        self._running = True

        try:
            # Create zulip client
            self._client = zulip.Client(
                site=self.config.site,
                email=self.config.bot_email,
                api_key=self.config.api_key,
            )

            logger.info("Zulip client connected to {}", self.config.site)

            # Register event queue for messages
            result = self._client.register(
                event_types=["message"],
                fetch_event_types=["message"],
            )
            self._queue_name = result.get("queue_id", "")
            last_event_id = result.get("last_event_id", -1)

            # Start event polling loop
            self._event_task = asyncio.create_task(self._poll_loop())

            logger.info("Zulip channel started")

        except Exception as e:
            logger.error("Failed to start Zulip client: {}", e)
            self._running = False
            return

    async def _poll_loop(self) -> None:
        """Poll Zulip server for events."""
        last_event_id = -1

        while self._running:
            try:
                # Get events from server
                result = await asyncio.to_thread(
                    self._client.get_events,
                    queue_id=self._queue_name,
                    last_event_id=last_event_id,
                )

                events = result.get("events", [])
                for event in events:
                    last_event_id = event.get("id", last_event_id)
                    await self._handle_event(event)

            except Exception as e:
                if self._running:
                    logger.warning("Zulip event poll error: {}. Reconnecting...", e)
                    await asyncio.sleep(5)
                    # Try to re-register
                    try:
                        result = self._client.register(event_types=["message"])
                        self._queue_name = result.get("queue_id", "")
                    except Exception:
                        pass

    async def _handle_event(self, event: dict) -> None:
        """Handle a Zulip event."""
        if event.get("type") != "message":
            return

        message = event.get("message", {})
        sender_id = str(message.get("sender_id", ""))
        sender_email = message.get("sender_email", "")

        # Check if message is from us (bot)
        if sender_email == self.config.bot_email:
            return

        # Check permissions
        if not self.is_allowed(sender_id) and not self.is_allowed(sender_email):
            logger.warning("Access denied for Zulip sender {} ({})", sender_id, sender_email)
            return

        content = message.get("content", "")
        msg_type = message.get("type", "")

        # Convert Zulip content to markdown
        content = _zulip_to_markdown(content)

        # Handle stream vs private messages
        if msg_type == "stream":
            stream = message.get("display_recipient", "")
            topic = message.get("subject", "")
            chat_id = f"stream:{stream}:{topic}"

            # Check stream permission
            if self.config.group_policy == "allowlist":
                if stream not in self.config.group_allow_from:
                    logger.debug("Stream {} not in allowlist", stream)
                    return
            elif self.config.group_policy == "mention":
                # Check for bot mention
                if f"@**{self.config.bot_email.split('@')[0]}**" not in message.get("content", ""):
                    if f"@_**{self.config.bot_email.split('@')[0]}**" not in message.get(
                        "content", ""
                    ):
                        logger.debug("No mention in stream message")
                        return

            metadata = {
                "stream": stream,
                "topic": topic,
                "message_id": message.get("id"),
                "zulip": {
                    "type": "stream",
                    "stream": stream,
                    "topic": topic,
                },
            }
        else:  # private message
            chat_id = f"private:{sender_id}"
            metadata = {
                "message_id": message.get("id"),
                "zulip": {
                    "type": "private",
                    "recipient_ids": message.get("to_user_ids", []),
                },
            }

        # Handle media attachments
        media = []
        for attachment in message.get("attachments", []):
            url = attachment.get("url", "")
            if url:
                media.append(url)

        await self._handle_message(
            sender_id=sender_id,
            chat_id=chat_id,
            content=content,
            media=media if media else None,
            metadata=metadata,
        )

    async def stop(self) -> None:
        """Stop the Zulip client."""
        self._running = False

        if self._event_task:
            self._event_task.cancel()
            try:
                await self._event_task
            except asyncio.CancelledError:
                pass
            self._event_task = None

        if self._client:
            self._client = None
            logger.info("Zulip client stopped")

    async def send(self, msg: OutboundMessage) -> None:
        """Send a message through Zulip."""
        if not self._client:
            logger.warning("Zulip client not running")
            return

        zulip_meta = msg.metadata.get("zulip", {}) if msg.metadata else {}
        msg_type = zulip_meta.get("type", "private")

        try:
            if msg_type == "stream":
                # Stream message
                stream = zulip_meta.get("stream", "")
                topic = zulip_meta.get("topic", "general")

                request = {
                    "type": "stream",
                    "to": stream,
                    "topic": topic,
                    "content": _markdown_to_zulip(msg.content),
                }
            else:
                # Private message
                # Try to parse the chat_id to get recipient
                if msg.chat_id.startswith("private:"):
                    recipient_id = msg.chat_id.split(":", 1)[1]
                    request = {
                        "type": "private",
                        "to": [int(recipient_id)],
                        "content": _markdown_to_zulip(msg.content),
                    }
                else:
                    # Fallback to chat_id as recipient
                    request = {
                        "type": "private",
                        "to": [msg.chat_id],
                        "content": _markdown_to_zulip(msg.content),
                    }

            result = await asyncio.to_thread(self._client.send_message, request)

            if result.get("result") != "success":
                logger.error("Failed to send Zulip message: {}", result.get("msg", "unknown error"))
            else:
                logger.debug("Zulip message sent: id={}", result.get("id"))

            # Handle media attachments
            for media_path in msg.media or []:
                try:
                    await asyncio.to_thread(self._client.upload_file, media_path)
                    # Note: Zulip requires the uploaded file to be referenced in message content
                    # We would need to get the upload URI and append it to content
                    logger.debug("Uploaded file to Zulip: {}", media_path)
                except Exception as e:
                    logger.error("Failed to upload file to Zulip: {}", e)

        except Exception as e:
            logger.error("Error sending Zulip message: {}", e)
