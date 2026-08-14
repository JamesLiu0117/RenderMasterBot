import unittest
from unittest.mock import Mock, patch

from render_master_bot.ollama import OllamaClient, OllamaError


class OllamaEmbeddingTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
