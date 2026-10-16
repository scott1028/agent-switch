# -I: ignore PYTHONPATH, user site-packages and the cwd, so the bundled agent_switch always loads.
{{ python-executable }} -I -m agent_switch "$@"
