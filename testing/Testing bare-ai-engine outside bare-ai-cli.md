# Testing bare-ai-engine outside bare-ai-cli (in case of errors_
# Notice Streaming works.

BARE_AI_ENDPOINT="http://100.64.0.13:11434/v1/chat/completions" BARE_AI_API_KEY="none" BARE_AI_MODEL="granite4:tiny-h" BARE_AI_NO_TOOLS="true" node $HOME/bare-ai-cli/bundle/bare-ai.js -p "tell me a joke about nerds."