"""
Databricks Genie Bot

Author: Luiz Carrossoni Neto
Revision: 1.0

This script implements an experimental chatbot that interacts with Databricks' Genie API. The bot facilitates conversations with Genie,
Databricks' AI assistant, through a chat interface.

Note: This is experimental code and is not intended for production use.


Update on May 02 to reflect Databricks API Changes https://www.databricks.com/blog/genie-conversation-apis-public-preview
"""

import os
import json
import logging
from dataclasses import dataclass
from dotenv import load_dotenv
from aiohttp import web
from botbuilder.core import BotFrameworkAdapterSettings, BotFrameworkAdapter, ActivityHandler, TurnContext, CardFactory
from botbuilder.schema import Activity, ActivityTypes, ChannelAccount
from databricks.sdk import WorkspaceClient, GenieAPI
from databricks.sdk.service.sql import StatementResponse
from databricks.sdk.service.dashboards import GenieResultMetadata
import asyncio

# Log
logger = logging.getLogger(__name__)

# Env vars
load_dotenv()

DATABRICKS_HOST = os.getenv("DATABRICKS_HOST")
DATABRICKS_CLIENT_ID = os.getenv("DATABRICKS_CLIENT_ID")
DATABRICKS_CLIENT_SECRET = os.getenv("DATABRICKS_CLIENT_SECRET")
APP_ID = os.getenv("APP_ID", "")
APP_PASSWORD = os.getenv("APP_PASSWORD", "")

# Constants
WELCOME_MESSAGE = "Welcome to the Data Query Bot!"
WAITING_MESSAGE = "Please wait while I process your request..."
SWITCHING_MESSAGE = "switch to @"

# Spaces mapping in json file
with open('spaces.json') as f:
    SPACES = json.load(f)

REVERSE_SPACES = {v: k for k, v in SPACES.items()}

LIST_SPACES = ", ".join([f"@{space_name}" for space_name in SPACES.keys()])
SPACE_NOT_FOUND = f"Genie space not found. Please use {LIST_SPACES} to specify the space."

workspace_client = WorkspaceClient(
    host=DATABRICKS_HOST,
    client_id=DATABRICKS_CLIENT_ID,
    client_secret=DATABRICKS_CLIENT_SECRET,
)

genie_api = GenieAPI(workspace_client.api_client)


@dataclass
class GenieResult:
    query_description: str | None = None
    query_result_metadata: GenieResultMetadata | None = None
    statement_id: str | None = None
    statement_response: StatementResponse | None = None
    message: str | None = None
    conversation_id: str | None = None


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


async def ask_genie(question: str, space_id: str, conversation_id: str | None) -> GenieResult:
    """    
    Asks a question to Genie and retrieves the response.

    This function interacts with the Genie API to either start a new conversation or continue an existing one. 
    It processes the response, including handling attachments and query results, and returns the relevant data.

    Args:
        question (str): The question to ask Genie.
        space_id (str): The ID of the Genie space to use.
        conversation_id (str | None): Optional conversation ID to continue an existing conversation. 
                                      If None, a new conversation will be started.

    Returns:
        tuple[GenieResult, str]: A tuple containing:
            - GenieResult: The response from Genie, which may include query results or message content.
            - str: The conversation ID, which can be used for subsequent interactions.

    Raises:
        Exception: If an error occurs during the interaction with the Genie API, it is logged, and an error response is returned.
    """
    try:
        loop = asyncio.get_running_loop()
        if conversation_id is None:
            initial_message = await loop.run_in_executor(None, genie_api.start_conversation_and_wait, space_id, question)
            conversation_id = initial_message.conversation_id
        else:
            initial_message = await loop.run_in_executor(
                None, genie_api.create_message_and_wait,
                space_id,
                conversation_id,
                question
            )

        message_content = await loop.run_in_executor(
            None,
            genie_api.get_message,
            space_id,
            initial_message.conversation_id,
            initial_message.message_id
        )

        logger.info(f"Raw message content: {message_content}")

        if not message_content.attachments:
            return {"message": message_content.content}, conversation_id

        for attachment in message_content.attachments:
            attachment_id = attachment.attachment_id
            query_obj = attachment.query
            if not attachment_id or not query_obj:
                text_obj = attachment.text
                if text_obj:
                    return {"message": text_obj.content}, conversation_id
                else:
                    return {"message": ""}, conversation_id

            # Use the new endpoint to get query results
            query_result = await loop.run_in_executor(
                None,
                genie_api.get_message_query_result_by_attachment,
                space_id,
                initial_message.conversation_id,
                initial_message.message_id,
                attachment_id
            )
            logger.info(f"Raw query result: {query_result}")

            response_data = GenieResult(
                query_description=query_obj.description,
                query_result_metadata=query_obj.query_result_metadata,
                statement_id=query_obj.statement_id,
                conversation_id=conversation_id
            )

            logger.info(
                f"Query result metadata: {response_data.query_result_metadata}")
            logger.info(f"Statement ID: {response_data.statement_id}")

            if query_result.statement_response:
                response_data.statement_response = query_result.statement_response
                logger.info(
                    f"Added statement_response to response: {response_data.statement_response}")
            else:
                logger.error(
                    f"Missing statement_response in query_result: {query_result}")
            return response_data

        return GenieResult(message=message_content.content, conversation_id=conversation_id)

    except Exception as e:
        logger.error(f"Error in ask_genie: {str(e)}")
        return {"error": "An error occurred while processing your request."}, conversation_id


def process_query_results(answer: GenieResult) -> Activity:
    """
    Processes the result from a Genie query and formats it into an Activity object.

    This function takes a GenieResult object, extracts relevant information such as 
    query description, metadata, and query results, and formats it into a message 
    activity. If the query result contains tabular data, it generates an adaptive 
    card with a table representation of the data.

    :param answer: A GenieResult object containing the query response.
    :type answer: GenieResult

    :returns: An Activity object containing the formatted response or an error message.
    :rtype: Activity

    :raises: Logs errors if required fields (e.g., result or data_array) are missing 
             in the GenieResult object.
    """
    response = ""

    logger.info(f"Processing answer JSON: {answer}")

    if answer.query_description:
        response += f"## Query Description\n\n{answer.query_description}\n\n"

    if answer.query_result_metadata:
        metadata = answer.query_result_metadata
        if metadata.row_count:
            response += f"**Row Count:** {metadata.row_count}\n\n"

    if answer.statement_response:
        statement_response = answer.statement_response
        logger.info(f"Found statement_response: {statement_response}")

        if statement_response.result and statement_response.result.data_array:

            manifest = statement_response.manifest
            columns = []

            if manifest and manifest.schema and manifest.schema.columns:
                columns = manifest.schema.columns
                logger.info(f"Schema columns: {columns}")
            else:
                logger.warning("No manifest found in statement_response.")

            col_output = [{"width": 3} for _ in columns]

            data_array = statement_response.result.data_array
            logger.info(f"Data array: {data_array}")

            row_output = [
                {
                    "type": "TableRow",
                    "cells": [
                        {
                            "type": "TableCell",
                            "items": [
                                {
                                    "type": "TextBlock",
                                    "text": col.name,
                                    "wrap": True,
                                }
                            ]
                        }
                        for col in columns
                    ]
                }
            ]

            for row in data_array:
                cell_output = []
                for value, col in zip(row, columns):
                    if value is None:
                        formatted_value = "NULL"
                    elif col.type_name in ["DECIMAL", "DOUBLE", "FLOAT"]:
                        formatted_value = f"{float(value):,.2f}"
                    elif col.type_name in ["INT", "BIGINT", "LONG"]:
                        formatted_value = f"{int(value):,}"
                    else:
                        formatted_value = str(value)
                    cell_output.append({
                        "type": "TableCell",
                        "items": [
                            {
                                "type": "TextBlock",
                                "text": formatted_value,
                                "wrap": True,
                            }
                        ]
                    })
                row_output.append({
                    "type": "TableRow",
                    "cells": cell_output
                })

                attachment = CardFactory.adaptive_card(
                    {
                        "type": "AdaptiveCard",
                        "version": "1.5",
                        "body": [
                            {
                                "type": "Table",
                                "roundedCorners": True,
                                "firstRowAsHeaders": True,
                                "columns": col_output,
                                "rows": row_output,
                            },
                        ],
                    }
                )
            return Activity(
                text=response,
                type=ActivityTypes.message,
                attachments=[attachment])
        else:
            logger.error(
                f"Missing result or data_array in statement_response: {statement_response}")
    elif answer.message:
        response += f"{answer.message}\n\n"
        return Activity(
            text=response,
            type=ActivityTypes.message,
        )
    else:
        response += "No data available.\n\n"
        logger.error("No statement_response or message found in answer_json")

    return Activity(
        text=response,
        type=ActivityTypes.message
    )


SETTINGS = BotFrameworkAdapterSettings(APP_ID, APP_PASSWORD)
ADAPTER = BotFrameworkAdapter(SETTINGS)


class MyBot(ActivityHandler):
    def __init__(self):
        self.conversation_ids: dict[str, str] = {}
        self.space_ids: dict[str, str] = {}

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
                self.conversation_ids[user_id] = None
        if SWITCHING_MESSAGE in question.lower():
            space_id = get_space_id(question)
            if space_id == SPACE_NOT_FOUND:
                await turn_context.send_activity(SPACE_NOT_FOUND)
                return
            self.space_ids[user_id] = space_id
            # Reset conversation ID for the new space
            self.conversation_ids[user_id] = None
            await turn_context.send_activity(f"Switched to space: {REVERSE_SPACES[space_id]}")
            return
        try:
            await turn_context.send_activity(WAITING_MESSAGE)
            genie_result = await ask_genie(question, space_id, conversation_id)
            self.conversation_ids[user_id] = genie_result.conversation_id
            response = process_query_results(genie_result)

            await turn_context.send_activity(response)
        except json.JSONDecodeError:
            await turn_context.send_activity("Failed to decode response from the server.")
        except Exception as e:
            logger.error(f"Error processing message: {str(e)}")
            await turn_context.send_activity("An error occurred while processing your request.")

    async def on_members_added_activity(self, members_added: list[ChannelAccount], turn_context: TurnContext):
        for member in members_added:
            if member.id != turn_context.activity.recipient.id:
                await turn_context.send_activity(WELCOME_MESSAGE)


BOT = MyBot()


async def messages(req: web.Request) -> web.Response:
    if "application/json" in req.headers["Content-Type"]:
        body = await req.json()
    else:
        return web.Response(status=415)

    activity = Activity().deserialize(body)
    auth_header = req.headers.get("Authorization", "")

    try:
        response = await ADAPTER.process_activity(activity, auth_header, BOT.on_turn)
        if response:
            return web.json_response(data=response.body, status=response.status)
        return web.Response(status=201)
    except Exception as e:
        logger.error(f"Error processing request: {str(e)}")
        return web.Response(status=500)

app = web.Application()
app.router.add_post("/api/messages", messages)

if __name__ == "__main__":
    try:
        host = os.getenv("HOST", "localhost")
        port = int(os.environ.get("PORT", 3978))
        web.run_app(app, host=host, port=port)
    except Exception as error:
        logger.exception("Error running app")
