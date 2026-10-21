This is the frozen Python implementation used to create fixtures and check
compatibility during the Rust migration. It is not part of the installed CLI.

The existing Python suite retains direct unit checks of this reference.
`OrgCase.cli` always invokes the Rust executable, selected by `RECKON_TEST_BIN`
or `target/debug/reckon`. Native behavior and security checks are in `native.rs`.
