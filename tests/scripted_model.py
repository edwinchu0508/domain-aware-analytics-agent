"""Offline SDK response fixtures, no API or database connection."""
from agents import Model, ModelResponse
from agents.usage import Usage
from openai.types.responses import ResponseOutputMessage, ResponseOutputText
def message(text):
    return ResponseOutputMessage(
        id="offline_message", role="assistant", status="completed",
        content=[ResponseOutputText(type="output_text", text=text, annotations=[])],
        type="message",
    )


class ScriptedModel(Model):
    """Only model responses are scripted; Runner and real tools are untouched."""
    def __init__(self, respond):
        self.respond = respond
        self.calls = []

    async def get_response(self, system_instructions, input, **kwargs):
        self.calls.append(input)
        output = self.respond(input, len(self.calls))
        return ModelResponse(output=output, usage=Usage(), response_id=None)

    async def stream_response(self, *args, **kwargs):
        raise AssertionError("Streaming is outside this test")
        yield
