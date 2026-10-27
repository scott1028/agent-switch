# Troubleshooting

Problems we have hit while running agent-switch, and how we fixed them. Each entry gives what you
see, how to check it is your problem, why it happens, and the fix.

- [dsh and dst crash with "No usable native binding found"](#dsh-and-dst-crash-with-no-usable-native-binding-found)

## dsh and dst crash with "No usable native binding found"

If DeepSeek Harness (`@deepseek-ai/dsh`) is installed globally with pnpm, both `agent-switch dsh`
and `agent-switch dst` stop right after they start. Remove the pnpm copy and use the npm one.

Seen on 2026-10-27 on Linux x64 with `@deepseek-ai/dsh` 0.2.1-rc.2, pnpm 11.0.8, Node.js 22.22.3
and `@deepseek-harness-tui/dsh-tui` 0.17.0.

### What you see

`agent-switch dsh` reaches the model server, then dsh exits with:

```text
dsh: fatal uncaught exception: Error: dsh: host preparation failed: No usable native binding found for node-addon-require-builtin-linux-x64-gnu (auto)
```

`agent-switch dst` (or `dsh-tui`) prints the same error. The TUI then says its dsh profile exited
and asks whether to enter safe mode:

```text
[dsh-tui] dsh profile 已退出（退出码 1）。可直接运行以下命令查看诊断：
  dsh --profile dsh-tui
dsh-tui 异常退出（码 1）。进入安全模式？[Y/n]
```

### Is this your problem?

List every `dsh` on your PATH, and pnpm's global bin folder:

```sh
which -a dsh
pnpm bin -g
```

It is your problem when the first `dsh` sits in the folder `pnpm bin -g` prints (on the machine
above, `~/.local/share/pnpm/bin`), and `pnpm ls -g` lists `@deepseek-ai/dsh`.

### Why it happens

Two things happen together: the pnpm copy of dsh runs instead of the npm copy, and the pnpm copy
cannot load a file it needs.

**The pnpm copy wins on PATH.** agent-switch starts the first `dsh` on PATH. For dst it also hands
that same `dsh` to dsh-tui, so both commands run the same copy.

```text
agent-switch dsh ──finds first dsh on PATH──► pnpm copy ──boots──► crash
agent-switch dst ──starts──► dsh-tui ──starts──► pnpm copy ──boots──► crash
```

pnpm's global bin folder came before npm's on that PATH. With the npm copy first, dst started
normally.

**The pnpm copy cannot load its native binding.** A native binding is a compiled `.node` file that
Node.js loads like a module. dsh gets one through `node-addon-require-builtin` 0.1.9, which ships
the Linux x64 file in a separate package, `node-addon-require-builtin-linux-x64-gnu`. A third
package, `node-addon-native-custom-loader` 0.1.9, loads it with a plain `require()` from its own
folder.

Think of `node_modules` as shelves. npm puts every package on one shared shelf, so the loader finds
the platform package next to itself. pnpm gives each package its own shelf that holds only what that
package declared, and the loader did not declare the platform package.

```text
npm: one shared folder (@deepseek-ai/dsh/node_modules/)
├── node-addon-native-custom-loader/           loader looks next to itself
└── node-addon-require-builtin-linux-x64-gnu/  found

pnpm: one folder per package (under the store's links/@/)
node-addon-native-custom-loader/0.1.9/<hash>/node_modules/
└── node-addon-native-custom-loader/           loader looks next to itself: nothing
node-addon-require-builtin/0.1.9/<hash>/node_modules/
└── node-addon-require-builtin-linux-x64-gnu/  the file is here
```

After that miss, the loader tries a local build at
`build/nodeabi/node-v127-linux-x64-gnu/require_builtin.node` and
`build/napi/napi-v9-linux-x64-gnu/require_builtin.node`. The published package has no `build/`
folder, so every attempt fails and dsh stops.

This is a bug in the upstream loader, not in agent-switch. The loader lives in
[deepseek-ai/dsh-node-addon-require-builtin](https://github.com/deepseek-ai/dsh-node-addon-require-builtin)
under `packages/loader`.

### Fix it

Remove the pnpm copy, then check which `dsh` is left:

```sh
pnpm remove -g @deepseek-ai/dsh
which -a dsh
```

If `which -a dsh` now prints nothing, install dsh with npm. This is the command agent-switch offers
on Linux and macOS:

```sh
npm install -g --prefix ~/.local @deepseek-ai/dsh
```

Things to remember:

- Do not install dsh with `pnpm install -g` or `pnpm add -g` until the loader is fixed upstream.
- `pnpm remove -g @deepseek-ai/dsh` removes only that package. pnpm itself and your other global
  pnpm packages stay.

### Check that it works

Start both again against your model server:

```sh
agent-switch dst --url http://127.0.0.1:8080
agent-switch dsh --url http://127.0.0.1:8080
```

- dst opens the full-screen TUI and waits for your prompt.
- dsh prints a line like `dsh web: http://127.0.0.1:3080/?token=...` and opens your default
  browser.

### Other fixes we did not pick

| Fix | Why we did not pick it |
|---|---|
| Put npm's global bin folder before pnpm's on PATH | It edits your shell config, and the broken pnpm copy stays installed. It runs again if the PATH order changes or someone calls it by full path. |
| Make agent-switch skip a broken `dsh` | It adds agent-switch code to work around an upstream bug. Removing the broken copy fixes the cause with no code change. |
