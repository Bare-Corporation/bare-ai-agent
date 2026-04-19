# Testing bare-ai-engine outside bare-ai-cli (in case of errors_
# Notice Streaming works.

BARE_AI_ENDPOINT="http://100.64.0.12:11434/v1/chat/completions" BARE_AI_API_KEY="none" BARE_AI_MODEL="llama3.1:8b" BARE_AI_NO_TOOLS="true" node /home/bare-ai/bare-ai-cli/bundle/gemini.js -p "tell me a joke about 
nerds."

BARE_AI_ENDPOINT="http://100.64.0.12:11434/v1/chat/completions" BARE_AI_API_KEY="none" BARE_AI_MODEL="granite4:tiny-h" BARE_AI_NO_TOOLS="true" node /home/bare-ai/bare-ai-cli/bundle/gemini.js -p "tell me a joke about nerds."

BARE_AI_ENDPOINT="http://100.64.0.12:11434/v1/chat/completions" BARE_AI_API_KEY="none" BARE_AI_MODEL="deepseek-r1:8b" BARE_AI_NO_TOOLS="true" node /home/bare-ai/bare-ai-cli/bundle/gemini.js -p "tell me a joke about nerds."

BARE_AI_ENDPOINT="http://100.64.0.12:11434/v1/chat/completions" BARE_AI_API_KEY="none" BARE_AI_MODEL="qwen2.5-coder:7b" BARE_AI_NO_TOOLS="true" node /home/bare-ai/bare-ai-cli/bundle/gemini.js -p "tell me a joke about nerds."

BARE_AI_ENDPOINT="http://100.64.0.12:11434/v1/chat/completions" BARE_AI_API_KEY="none" BARE_AI_MODEL="qwen2.5-coder:14b" BARE_AI_NO_TOOLS="true" node /home/bare-ai/bare-ai-cli/bundle/gemini.js -p "tell me a joke about nerds."

BARE_AI_ENDPOINT="http://100.64.0.12:11434/v1/chat/completions" BARE_AI_API_KEY="none" BARE_AI_MODEL="qwen2.5-coder:32b" BARE_AI_NO_TOOLS="true" node /home/bare-ai/bare-ai-cli/bundle/gemini.js -p "tell me a joke about nerds."

BARE_AI_ENDPOINT="http://100.64.0.12:11434/v1/chat/completions" BARE_AI_API_KEY="none" BARE_AI_MODEL="qwen3.5:0.8b" BARE_AI_NO_TOOLS="true" node /home/bare-ai/bare-ai-cli/bundle/gemini.js -p "tell me a joke about nerds."

BARE_AI_ENDPOINT="http://100.64.0.12:11434/v1/chat/completions" BARE_AI_API_KEY="none" BARE_AI_MODEL="qwen3.5:4b" BARE_AI_NO_TOOLS="true" node /home/bare-ai/bare-ai-cli/bundle/gemini.js -p "tell me a joke about nerds."

BARE_AI_ENDPOINT="http://100.64.0.12:11434/v1/chat/completions" BARE_AI_API_KEY="none" BARE_AI_MODEL="gemma4:e4b" BARE_AI_NO_TOOLS="true" node /home/bare-ai/bare-ai-cli/bundle/gemini.js -p "tell me a joke about nerds."

BARE_AI_ENDPOINT="http://100.64.0.12:11434/v1/chat/completions" BARE_AI_API_KEY="none" BARE_AI_MODEL="gemma4:26b" BARE_AI_NO_TOOLS="true" node /home/bare-ai/bare-ai-cli/bundle/gemini.js -p "tell me a joke about nerds."

BARE_AI_ENDPOINT="http://100.64.0.12:11434/v1/chat/completions" BARE_AI_API_KEY="none" BARE_AI_MODEL="gemma4:31b" BARE_AI_NO_TOOLS="true" node /home/bare-ai/bare-ai-cli/bundle/gemini.js -p "tell me a joke about nerds."

BARE_AI_ENDPOINT="http://100.64.0.12:11434/v1/chat/completions" BARE_AI_API_KEY="none" BARE_AI_MODEL="mistral-nemo" BARE_AI_NO_TOOLS="true" node /home/bare-ai/bare-ai-cli/bundle/gemini.js -p "tell me a joke about nerds."
