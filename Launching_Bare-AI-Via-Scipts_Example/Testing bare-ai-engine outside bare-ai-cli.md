# 1) Testing bare-ai-engine outside bare-ai-cli UI:

## 1.1) CLOUD AIs:

### 1.1.1) Deepseek R4


BARE_AI_ENDPOINT="https://api.deepseek.com/v1/chat/completions" BARE_AI_API_KEY="sk-redacted" BARE_AI_MODEL="deepseek-v4-pro" BARE_AI_NO_TOOLS="true" node $HOME/bare-ai-cli/bundle/bare-ai.js -p "tell me a joke about nerds."


### 1.1.2) Claude Sonnet

BARE_AI_ENDPOINT="https://api.anthropic.com/v1/chat/completions" BARE_AI_API_KEY="sk-ant-redacted" BARE_AI_MODEL="claude-sonnet-4-6" BARE_AI_NO_TOOLS="true" node $HOME/bare-ai-cli/bundle/bare-ai.js -p "tell me a joke about nerds."

## 1.2) LOCALLY HOSTED AIs:

### 1.2.1) IBM Granite


BARE_AI_ENDPOINT="http://100.64.0.13:11434/v1/chat/completions" BARE_AI_API_KEY="none" BARE_AI_MODEL="granite4:tiny-h" BARE_AI_NO_TOOLS="true" node $HOME/bare-ai-cli/bundle/bare-ai.js -p "tell me a joke about nerds."