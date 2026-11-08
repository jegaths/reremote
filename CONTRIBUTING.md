# Contributing

Thanks for your interest in ReRemote!

## Ways to help

- **Remote compatibility reports.** Open an issue saying which remote you
  tried (brand, model, the chip on its PCB if you can see it), whether it
  decoded as `rcswitch-<n>` or `RAW`, and whether repeat presses were
  recognised. These are very useful.
- **Bug reports.** Include your OS, board, receiver module, and the relevant
  part of the Serial log panel.
- **Pull requests.** For anything bigger than a small fix, please open an
  issue first so we can agree on the approach.

## Development

```sh
make setup
make sim     # UI with a fake board
make test    # run the test suite before sending a PR
```

See [docs/how-it-works.md](docs/how-it-works.md) for the code layout and API.

## Contributor licence terms

ReRemote is published under the GNU AGPL v3.0. To keep the project able to
offer other licensing options in future (for example a commercial licence that
helps fund development), contributions need these terms:

By submitting a contribution (a pull request, patch, or other code or
documentation) to this project, you confirm that:

1. You wrote the contribution yourself, or otherwise have the right to submit
   it under these terms.
2. You license your contribution to the public under the GNU AGPL v3.0, the
   same licence as the rest of the project.
3. You also grant **Jegath S** (the project maintainer) a perpetual,
   worldwide, non-exclusive, royalty-free, irrevocable licence to use, copy,
   modify, sublicense and distribute your contribution under any other licence
   terms, including proprietary ones.
4. You keep the copyright to your contribution, and you may use it however you
   like.

Please state this in your pull request by including the line:

```
I agree to the contributor licence terms in CONTRIBUTING.md.
```
