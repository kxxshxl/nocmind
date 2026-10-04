from dataclasses import replace

import pytest

import app.services.llm as llm_module
from app.services.llm import LLMClient


class FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            'choices': [
                {
                    'message': {
                        'content': 'tool selected',
                        'tool_calls': [
                            {
                                'id': 'call-1',
                                'type': 'function',
                                'function': {
                                    'name': 'get_network_metrics',
                                    'arguments': '{"device":"router-mum-04"}',
                                },
                            }
                        ],
                    }
                }
            ],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 4},
        }


class FakeClient:
    def __init__(self, *args, **kwargs):
        self.url = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, headers, json):
        self.url = url
        assert json['tool_choice'] == 'auto'
        assert json['tools'][0]['function']['name'] == 'get_network_metrics'
        return FakeResponse()


@pytest.mark.asyncio
async def test_external_llm_parses_tool_calls(monkeypatch):
    monkeypatch.setattr(llm_module, 'settings', replace(llm_module.settings,
        llm_provider='mock',
        llm_base_url='http://mock/chat/completions',
        llm_api_key='test-key',
        llm_model='mock-model',
        llm_api_style='openai',
        llm_timeout_seconds=5,
        llm_fallback_provider='',
    ))
    monkeypatch.setattr('httpx.AsyncClient', FakeClient)

    response = await LLMClient().chat(
        [{'role': 'user', 'content': 'inspect router'}],
        tools=[
            {
                'type': 'function',
                'function': {
                    'name': 'get_network_metrics',
                    'description': 'Read network metrics',
                    'parameters': {
                        'type': 'object',
                        'properties': {'device': {'type': 'string'}},
                    },
                },
            }
        ],
    )

    assert response.content == 'tool selected'
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == 'get_network_metrics'
    assert response.tool_calls[0].arguments == {'device': 'router-mum-04'}
    assert response.usage == {'input_tokens': 10, 'output_tokens': 4}
