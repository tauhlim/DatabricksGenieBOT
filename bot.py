import json
import logging

from botbuilder.core import ActivityHandler, TurnContext
from botbuilder.schema import ChannelAccount

from adaptive_card import AdaptiveCardFactory
from const import SPACE_NOT_FOUND, SWITCHING_MESSAGE, REVERSE_SPACES, WELCOME_MESSAGE, SPACES
from genie import GenieQuerier

# Log
logger = logging.getLogger(__name__)


class MyBot(ActivityHandler):
    conversation_ids: dict[str, str]
    space_ids: dict[str, str]
    genie_querier: GenieQuerier

    def __init__(self):
        self.conversation_ids: dict[str, str] = {}
        self.space_ids: dict[str, str] = {}
        self.genie_querier = GenieQuerier()

    async def on_message_activity(self, turn_context: TurnContext):
        """
        Handles incoming messages from the user.
        It processes the message, determines the appropriate Genie space,
        and sends the query to Genie for processing.
        :param turn_context: The context of the turn, containing the activity.
        :return: None
        """
        logger.info(f"Received message: {turn_context.activity.text}")
        question = turn_context.activity.text
        user_id = turn_context.activity.from_property.id
        conversation_id = self.conversation_ids.get(user_id)
        space_id = self.space_ids.get(user_id)
        if not space_id or "@" in question.lower():
            new_space_id = get_space_id(question)
            if new_space_id == SPACE_NOT_FOUND:
                await turn_context.send_activity(SPACE_NOT_FOUND)
                return
            # users want to switch spaces
            if new_space_id != space_id:
                space_id = new_space_id
                conversation_id = None
                self.space_ids[user_id] = new_space_id
                self.conversation_ids.pop(user_id, None)
        if SWITCHING_MESSAGE in question.lower():
            space_id = get_space_id(question)
            if space_id == SPACE_NOT_FOUND:
                await turn_context.send_activity(SPACE_NOT_FOUND)
                return
            self.space_ids[user_id] = space_id
            # Reset conversation ID for the new space
            self.conversation_ids.pop(user_id, None)
            await turn_context.send_activity(f"Switched to space: {REVERSE_SPACES[space_id]}")
            return
        try:
            wait_activity = await turn_context.send_activity(AdaptiveCardFactory.get_waiting_message())
            genie_result = await self.genie_querier.ask_genie(question, space_id, conversation_id)
            self.conversation_ids[user_id] = genie_result.conversation_id
            response_activity = genie_result.process_query_results()
            response_activity.id = wait_activity.id  # Use the same ID to update the waiting message
            await turn_context.update_activity(response_activity)
        except json.JSONDecodeError:
            await turn_context.send_activity("Failed to decode response from the server.")
        except Exception as e:
            logger.error(f"Error processing message: {str(e)}")
            await turn_context.send_activity("An error occurred while processing your request.")

    async def on_members_added_activity(self, members_added: list[ChannelAccount], turn_context: TurnContext):
        for member in members_added:
            if member.id != turn_context.activity.recipient.id:
                await turn_context.send_activity(WELCOME_MESSAGE)


def get_space_id(question: str) -> str:
    """
    Determines the Genie space ID based on the question.
    :param question: The question to analyze for space ID.
    :return: The space ID if found, otherwise a message indicating space not found.
    """
    for space_name, space_id in SPACES.items():
        if "@" + space_name.lower() in question.lower():
            return space_id
    return SPACE_NOT_FOUND
