import json
import logging

from botbuilder.core import ActivityHandler, TurnContext, ConversationState, UserState
from botbuilder.dialogs import Dialog
from botbuilder.schema import ChannelAccount

from adaptive_card import AdaptiveCardFactory
from const import (
    SPACE_NOT_FOUND,
    SWITCHING_MESSAGE,
    REVERSE_SPACES,
    WELCOME_MESSAGE,
    SPACES,
    OAUTH_CONNECTION_NAME,
)
from genie import GenieQuerier
from helpers.dialog_helper import DialogHelper

# Log
logger = logging.getLogger(__name__)


class MyBot(ActivityHandler):
    # conversation_ids: dict[str, str]
    # space_ids: dict[str, str]
    # genie_querier: GenieQuerier

    def __init__(self,
        conversation_state: ConversationState,
        user_state: UserState,
        dialog: Dialog,):
        self.conversation_ids: dict[str, str] = {}
        self.space_ids: dict[str, str] = {}
        self.genie_querier = GenieQuerier()
        self.conversation_state = conversation_state
        self.user_state = user_state
        self.dialog = dialog

    async def on_message_activity(self, turn_context: TurnContext):
        """
        Handles incoming messages from the user.
        It processes the message, determines the appropriate Genie space,
        and sends the query to Genie for processing.
        :param turn_context: The context of the turn, containing the activity.
        :return: None
        """
        logger.info(f"Received message: {turn_context.activity.text}")
        
        if not turn_context.activity.from_property or not isinstance(turn_context.activity.from_property.id, str):
            logger.warning("No valid user identifier found")
            await turn_context.send_activity("Unable to identify user. Please try again.")
            return
        
        # Check if user is authenticated
        if not await self._is_user_authenticated(turn_context):
            logger.info("User not authenticated, triggering login dialog")
            await self._trigger_login_dialog(turn_context)
            return
        
        question = turn_context.activity.text
        user_id = str(turn_context.activity.from_property.id)
        conversation_id = self.conversation_ids.get(user_id)
        space_id = self.space_ids.get(user_id, "")
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
            await turn_context.send_activity(
                f"Switched to space: {REVERSE_SPACES[space_id]}"
            )
            return
        try:
            wait_activity = await turn_context.send_activity(
                AdaptiveCardFactory.get_waiting_message()
            )
            genie_result = await self.genie_querier.ask_genie(
                question, space_id, conversation_id
            )
            self.conversation_ids[user_id] = genie_result.conversation_id
            response_activity = genie_result.process_query_results()
            response_activity.id = (
                wait_activity.id
            )  # Use the same ID to update the waiting message
            await turn_context.update_activity(response_activity)
        except json.JSONDecodeError:
            await turn_context.send_activity(
                "Failed to decode response from the server."
            )
        except Exception as e:
            logger.error(f"Error processing message: {str(e)}")
            await turn_context.send_activity(
                "An error occurred while processing your request."
            )

    async def on_members_added_activity(
        self, members_added: list[ChannelAccount], turn_context: TurnContext
    ):
        for member in members_added:
            if member.id != turn_context.activity.recipient.id:
                await turn_context.send_activity(WELCOME_MESSAGE)

    async def on_turn(self, turn_context: TurnContext):
        await super().on_turn(turn_context)

        # Save any state changes that might have occurred during the turn.
        await self.conversation_state.save_changes(turn_context, False)
        await self.user_state.save_changes(turn_context, False)

    # Handle token response for web chat
    async def on_token_response_event(self, turn_context: TurnContext):
        # Run the Dialog with the new Token Response Event Activity.
        await DialogHelper.run_dialog(
            self.dialog,
            turn_context,
            self.conversation_state.create_property("DialogState"),
        )

    # Handle teams signin/verifyState invoke activity
    async def on_teams_signin_verify_state(self, turn_context: TurnContext):
        # Run the Dialog with the new Token Response Event Activity.
        # The OAuth Prompt needs to see the Invoke Activity in order to complete the login process.
        await DialogHelper.run_dialog(
            self.dialog,
            turn_context,
            self.conversation_state.create_property("DialogState"),
        )
    
    # on_invoke_activity does not handle signin/verifyState, so we need to handle it here
    async def on_invoke_activity(self, turn_context: TurnContext):
        if turn_context.activity.name == 'signin/verifyState':
            return await self.on_teams_signin_verify_state(turn_context)
        else:
            return await super().on_invoke_activity(turn_context)

    async def _is_user_authenticated(self, turn_context: TurnContext) -> bool:
        """
        Check if the user is authenticated by attempting to get their token.
        :param turn_context: The context of the turn.
        :return: True if authenticated, False otherwise.
        """
        try:
            token_response = await turn_context.adapter.get_user_token(
                turn_context, OAUTH_CONNECTION_NAME
            )
            # Update the genie querier with the new token
            self.genie_querier = GenieQuerier(token = token_response.token)
            return token_response is not None and token_response.token is not None
        except Exception as e:
            logger.error(f"Error checking authentication: {str(e)}")
            return False

    async def _trigger_login_dialog(self, turn_context: TurnContext):
        """
        Trigger the login dialog to authenticate the user.
        :param turn_context: The context of the turn.
        """
        try:
            await DialogHelper.run_dialog(
                self.dialog,
                turn_context,
                self.conversation_state.create_property("DialogState"),
            )
        except Exception as e:
            logger.error(f"Error triggering login dialog: {str(e)}")
            await turn_context.send_activity(
                "An error occurred while trying to authenticate. Please try again."
            )


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
