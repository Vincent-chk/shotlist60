SOURCE ?= sources/coffee.md
TODAY  := $(shell date +%Y-%m-%d)

.PHONY: test audit run prompt

# 审计器自测
test:
	python3 -m unittest discover -s tests -v

# 复验 runs/ 下所有记录（不需要 API Key）
audit:
	python3 audit_all.py

# 调用模型生成并审计（需要 SHOTLIST_API_KEY）
run:
	python3 run.py --source $(SOURCE) --out runs/$(TODAY)-demo

# 只打印完整提示词，粘到方舟控制台用
prompt:
	python3 run.py --print-prompt --source $(SOURCE)
