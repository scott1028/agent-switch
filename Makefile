.DEFAULT_GOAL := help
.PHONY: help references-mainline references-cluster

help:
	@echo "make references-mainline  update every references/* submodule to the latest commit of its branch"
	@echo "make references-cluster   start an easy-herdr agent cluster that upgrades this project to match references/*"

references-mainline:
	git submodule update --init --remote --depth 1 references
	git submodule status references

# The prompt stays on one line: easy-herdr types it into the first agent.
references-cluster:
	@command -v easy-herdr >/dev/null 2>&1 || { \
		echo "easy-herdr is not installed. Download it from https://github.com/scott1028/easy-herdr/releases and put it on PATH." >&2; \
		exit 1; \
	}
	@printf 'Agent CLI for planner, executor and reviewer1 (default: claude): '; read -r team_cli; team_cli=$${team_cli:-claude}; \
	printf 'Agent CLI for reviewer2 (default: codex): '; read -r reviewer2_cli; reviewer2_cli=$${reviewer2_cli:-codex}; \
	echo "easy-herdr cluster $$team_cli $$team_cli $$team_cli $$reviewer2_cli"; \
	easy-herdr cluster "$$team_cli" "$$team_cli" "$$team_cli" "$$reviewer2_cli" -- \
		"You are the planner. Read references/CLUSTER.md and follow it. planner, executor and reviewer1 run $$team_cli; reviewer2 runs $$reviewer2_cli."
