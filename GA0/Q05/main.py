import os
import sys
import traceback
from io import StringIO

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from openai import OpenAI


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CodeRequest(BaseModel):
    code: str


class ErrorAnalysis(BaseModel):
    error_lines: list[int]


def execute_python_code(code: str) -> dict:
    old_stdout = sys.stdout
    sys.stdout = StringIO()

    try:
        exec(code)

        output = sys.stdout.getvalue()

        return {
            "success": True,
            "output": output
        }

    except Exception:
        output = traceback.format_exc()

        return {
            "success": False,
            "output": output
        }

    finally:
        sys.stdout = old_stdout


def analyze_error_with_ai(code: str, traceback_text: str) -> list[int]:

    client = OpenAI(
        api_key=os.environ["AIPIPE_TOKEN"],
        base_url="https://aipipe.org/openrouter/v1"
    )

    prompt = f"""
Analyze this Python code and its error traceback.

Identify the exact line number or line numbers in the user's code
where the error occurred.

CODE:
{code}

TRACEBACK:
{traceback_text}

Return JSON with exactly this structure:
{{
    "error_lines": [3]
}}

Only include the relevant Python source-code line numbers.
"""

    response = client.chat.completions.create(
        # model="google/gemini-2.0-flash-lite-001",
        # model="google/gemini-2.0-flash-001",
        model="google/gemini-2.5-flash-lite",
        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ],
        response_format={
            "type": "json_object"
        }
    )

    result = ErrorAnalysis.model_validate_json(
        response.choices[0].message.content
    )

    return result.error_lines


@app.post("/code-interpreter")
def code_interpreter(request: CodeRequest):

    execution = execute_python_code(request.code)

    # Successful execution - DO NOT call AI
    if execution["success"]:
        return {
            "error": [],
            "result": execution["output"]
        }

    # Error occurred - ask AI for line numbers
    error_lines = analyze_error_with_ai(
        request.code,
        execution["output"]
    )

    return {
        "error": error_lines,
        "result": execution["output"]
    }
