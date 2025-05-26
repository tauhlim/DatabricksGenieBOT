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
from typing import Dict, List, Optional
from dotenv import load_dotenv
from aiohttp import web
from botbuilder.core import BotFrameworkAdapterSettings, BotFrameworkAdapter, ActivityHandler, TurnContext
from botbuilder.schema import Activity, ChannelAccount
from databricks.sdk import WorkspaceClient, GenieAPI
import asyncio

# Log
logging.basicConfig(level=logging.ERROR)
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


def get_space_id(question: str) -> str:
    # Select the right space based on the question
    for space_name, space_id in SPACES.items():
        if "@" + space_name.lower() in question.lower():
            return space_id
        return SPACE_NOT_FOUND

async def ask_genie(question: str, space_id: str, conversation_id: Optional[str] = None) -> tuple[str, str]:
    try:
        loop = asyncio.get_running_loop()
        if conversation_id is None:
            initial_message = await loop.run_in_executor(None, genie_api.start_conversation_and_wait, space_id, question)
            conversation_id = initial_message.conversation_id
        else:
            initial_message = await loop.run_in_executor(None, genie_api.create_message_and_wait, space_id, conversation_id, question)

        message_content = await loop.run_in_executor(None, genie_api.get_message,
                                                     space_id, initial_message.conversation_id, initial_message.message_id)

        logger.info(f"Raw message content: {message_content}")

        if message_content.attachments:
            for attachment in message_content.attachments:
                attachment_id = getattr(attachment, "attachment_id", None)
                query_obj = getattr(attachment, "query", None)
                if attachment_id and query_obj:
                    # Use the new endpoint to get query results
                    query_result = await loop.run_in_executor(
                        None,
                        genie_api.get_message_query_result,
                        space_id,
                        initial_message.conversation_id,
                        initial_message.message_id,
                        attachment_id
                    )
                    logger.info(f"Raw query result: {query_result}")

                    query_description = getattr(query_obj, "description", "")
                    query_result_metadata = getattr(
                        query_obj, "query_result_metadata", {})
                    statement_id = getattr(query_obj, "statement_id", "")

                    if hasattr(query_result_metadata, "__dict__"):
                        query_result_metadata = query_result_metadata.__dict__

                    logger.info(
                        f"Query result metadata: {query_result_metadata}")
                    logger.info(f"Statement ID: {statement_id}")

                    response_data = {
                        "query_description": query_description,
                        "query_result_metadata": query_result_metadata,
                        "statement_id": statement_id
                    }

                    if isinstance(query_result, dict) and "statement_response" in query_result:
                        response_data["statement_response"] = query_result["statement_response"]
                        logger.info(
                            f"Added statement_response to response: {response_data['statement_response']}")
                    else:
                        logger.error(
                            f"Missing statement_response in query_result: {query_result}")

                    return json.dumps(response_data), conversation_id

                text_obj = getattr(attachment, "text", None)
                if text_obj and hasattr(text_obj, "content"):
                    return json.dumps({"message": text_obj.content}), conversation_id

        return json.dumps({"message": message_content.content}), conversation_id
    except Exception as e:
        logger.error(f"Error in ask_genie: {str(e)}")
        return json.dumps({"error": "An error occurred while processing your request."}), conversation_id


def process_query_results(answer_json: Dict) -> str:
    response = ""

    logger.info(f"Processing answer JSON: {answer_json}")

    if "query_description" in answer_json and answer_json["query_description"]:
        response += f"## Query Description\n\n{answer_json['query_description']}\n\n"

    if "query_result_metadata" in answer_json:
        metadata = answer_json["query_result_metadata"]
        if isinstance(metadata, dict):
            if "row_count" in metadata:
                response += f"**Row Count:** {metadata['row_count']}\n\n"
            if "execution_time_ms" in metadata:
                response += f"**Execution Time:** {metadata['execution_time_ms']}ms\n\n"

    if "statement_response" in answer_json:
        statement_response = answer_json["statement_response"]
        logger.info(f"Found statement_response: {statement_response}")

        if "result" in statement_response and "data_array" in statement_response["result"]:
            response += "## Query Results\n\n"

            schema = statement_response.get("manifest", {}).get("schema", {})
            columns = schema.get("columns", [])
            logger.info(f"Schema columns: {columns}")

            header = "| " + " | ".join(col["name"] for col in columns) + " |"
            separator = "|" + "|".join(["---" for _ in columns]) + "|"
            response += header + "\n" + separator + "\n"

            data_array = statement_response["result"]["data_array"]
            logger.info(f"Data array: {data_array}")

            for row in data_array:
                formatted_row = []
                for value, col in zip(row, columns):
                    if value is None:
                        formatted_value = "NULL"
                    elif col["type_name"] in ["DECIMAL", "DOUBLE", "FLOAT"]:
                        formatted_value = f"{float(value):,.2f}"
                    elif col["type_name"] in ["INT", "BIGINT", "LONG"]:
                        formatted_value = f"{int(value):,}"
                    else:
                        formatted_value = str(value)
                    formatted_row.append(formatted_value)
                response += "| " + " | ".join(formatted_row) + " |\n"
        else:
            logger.error(
                f"Missing result or data_array in statement_response: {statement_response}")
    elif "message" in answer_json:
        response += f"{answer_json['message']}\n\n"
    else:
        response += "No data available.\n\n"
        logger.error("No statement_response or message found in answer_json")

    return response


SETTINGS = BotFrameworkAdapterSettings(APP_ID, APP_PASSWORD)
ADAPTER = BotFrameworkAdapter(SETTINGS)

class MyBot(ActivityHandler):
    def __init__(self):
        self.conversation_ids: Dict[str, str] = {}
        self.space_ids: Dict[str, str] = {}

    async def on_message_activity(self, turn_context: TurnContext):
        await turn_context.send_activity(WAITING_MESSAGE)
        logger.info(f"Received message: {turn_context.activity.text}")
        question = turn_context.activity.text
        user_id = turn_context.activity.from_property.id
        conversation_id = self.conversation_ids.get(user_id)
        space_id = self.space_ids.get(user_id)
        if not space_id or "switch to @" in question.lower():
            space_id = get_space_id(question)
            if space_id == SPACE_NOT_FOUND:
                await turn_context.send_activity(SPACE_NOT_FOUND)
                return
            self.space_ids[user_id] = space_id
        if "switch to @" in question.lower():
            space_id = get_space_id(question)
            if space_id == SPACE_NOT_FOUND:
                await turn_context.send_activity(SPACE_NOT_FOUND)
                return
            self.space_ids[user_id] = space_id
            await turn_context.send_activity(f"Switched to space: {REVERSE_SPACES[space_id]}")
            return
        try:
            answer, new_conversation_id = await ask_genie(question, space_id, conversation_id)
            self.conversation_ids[user_id] = new_conversation_id

            answer_json = json.loads(answer)
            response = process_query_results(answer_json)

            await turn_context.send_activity(response)
        except json.JSONDecodeError:
            await turn_context.send_activity("Failed to decode response from the server.")
        except Exception as e:
            logger.error(f"Error processing message: {str(e)}")
            await turn_context.send_activity("An error occurred while processing your request.")

    async def on_members_added_activity(self, members_added: List[ChannelAccount], turn_context: TurnContext):
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
