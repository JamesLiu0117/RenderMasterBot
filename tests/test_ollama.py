import unittest
from unittest.mock import Mock, patch

from render_master_bot.ollama import InstalledModel, OllamaClient, OllamaError


class OllamaEmbeddingTests(unittest.TestCase):
    def test_qwen35_defaults_to_no_thinking_but_honors_override(self):
        response = Mock()
        response.json.return_value = {"message": {"content": "{}"}}
        with patch("render_master_bot.ollama.httpx.post", return_value=response) as post:
            for override, expected in ((None, False), (True, True)):
                OllamaClient().chat_structured(
                    model="qwen3.5:9b", messages=[], json_schema={}, think=override,
                )
                self.assertIs(post.call_args.kwargs["json"]["think"], expected)

    def test_gpt_oss_defaults_to_low_thinking_and_releases_residency(self):
        response = Mock()
        response.json.return_value = {"message": {"content": "{}"}}
        with patch("render_master_bot.ollama.httpx.post", return_value=response) as post:
            OllamaClient().chat_structured(
                model="gpt-oss:20b", messages=[], json_schema={}, num_predict=1024,
            )
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["think"], "low")
        self.assertEqual(payload["keep_alive"], 0)
        self.assertEqual(payload["options"]["num_predict"], 1024)

    def test_invalid_generation_budget_never_calls_ollama(self):
        with patch("render_master_bot.ollama.httpx.post") as post:
            for limit in (0, -1, True, 1.5):
                with self.assertRaises(OllamaError):
                    OllamaClient().chat_structured(model="fake", messages=[], json_schema={}, num_predict=limit)
        post.assert_not_called()

    def test_model_details_preserve_digest_and_size(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "models": [
                {
                    "name": "planner:latest",
                    "digest": "a" * 64,
                    "size": 1234,
                }
            ]
        }
        with patch("render_master_bot.ollama.httpx.get", return_value=response):
            client = OllamaClient()
            self.assertEqual(
                client.list_model_details(),
                [InstalledModel(name="planner:latest", digest="a" * 64, size_bytes=1234)],
            )
            self.assertEqual(client.list_models(), ["planner:latest"])

    def test_model_details_reject_incomplete_identity(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "models": [{"name": "planner:latest", "size": 1234}]
        }
        with patch("render_master_bot.ollama.httpx.get", return_value=response):
            with self.assertRaisesRegex(OllamaError, "invalid record"):
                OllamaClient().list_model_details()

    def test_embedding_request_preserves_input_order(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"embeddings": [[1, 0], [0, 1]]}
        with patch("render_master_bot.ollama.httpx.post", return_value=response) as post:
            values = OllamaClient().embed_texts(
                model="qwen3-embedding:0.6b",
                texts=["door", "character"],
            )

        self.assertEqual(values, [[1.0, 0.0], [0.0, 1.0]])
        self.assertEqual(post.call_args.kwargs["json"]["input"], ["door", "character"])

    def test_embedding_count_mismatch_is_rejected(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"embeddings": [[1, 0]]}
        with patch("render_master_bot.ollama.httpx.post", return_value=response):
            with self.assertRaisesRegex(OllamaError, "count did not match"):
                OllamaClient().embed_texts(model="fake", texts=["one", "two"])

    def test_large_embedding_inputs_are_batched_without_reordering(self):
        def response_for_batch(*_, **kwargs):
            values = kwargs["json"]["input"]
            response = Mock()
            response.raise_for_status.return_value = None
            response.json.return_value = {
                "embeddings": [[float(value.removeprefix("item-"))] for value in values]
            }
            return response

        texts = [f"item-{index}" for index in range(33)]
        with patch(
            "render_master_bot.ollama.httpx.post",
            side_effect=response_for_batch,
        ) as post:
            values = OllamaClient().embed_texts(model="fake", texts=texts)

        self.assertEqual(values, [[float(index)] for index in range(33)])
        self.assertEqual(post.call_count, 2)
        self.assertEqual(len(post.call_args_list[0].kwargs["json"]["input"]), 32)
        self.assertEqual(post.call_args_list[1].kwargs["json"]["input"], ["item-32"])

    def test_structured_chat_preserves_base64_images(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "message": {"content": "{}"},
            "model": "vision-model",
        }
        messages = [{"role": "user", "content": "inspect", "images": ["cG5n"]}]
        with patch("render_master_bot.ollama.httpx.post", return_value=response) as post:
            result = OllamaClient().chat_structured(
                model="vision-model",
                messages=messages,
                json_schema={"type": "object"},
                think=False,
            )

        self.assertEqual(result.model, "vision-model")
        self.assertIsNone(result.done_reason)
        self.assertEqual(post.call_args.kwargs["json"]["messages"], messages)
        self.assertIs(post.call_args.kwargs["json"]["think"], False)

    def test_structured_chat_preserves_done_reason(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "message": {"content": "{}"},
            "model": "text-model",
            "done_reason": "stop",
        }
        with patch("render_master_bot.ollama.httpx.post", return_value=response):
            result = OllamaClient().chat_structured(
                model="text-model",
                messages=[{"role": "user", "content": "plan"}],
                json_schema={"type": "object"},
            )

        self.assertEqual(result.done_reason, "stop")

    def test_empty_final_content_is_reported_with_bounded_diagnostics(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "message": {"content": "", "thinking": "internal trace"},
            "done_reason": "length",
        }
        with patch("render_master_bot.ollama.httpx.post", return_value=response):
            with self.assertRaisesRegex(
                OllamaError,
                "done_reason='length', thinking_present=True",
            ):
                OllamaClient().chat_structured(
                    model="vision-model",
                    messages=[{"role": "user", "content": "inspect"}],
                    json_schema={"type": "object"},
                    think=False,
                )


if __name__ == "__main__":
    unittest.main()
