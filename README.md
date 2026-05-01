# Chimeric OS Hardware Control Suite

This repository contains the `nexus_deploy.py` hardware diagnostic and orchestration tool, designed for Kali Linux and tailored for the Chimeric OS toolchain.

## Prerequisites

Before running the suite, ensure you have the necessary system-level dependencies installed for smart card communication:

```bash
sudo apt-get update
sudo apt-get install pcscd libpcsclite-dev swig
```

You must also have [uv](https://github.com/astral-sh/uv) installed to handle inline script dependencies.

## Execution

The script is entirely self-contained and uses PEP 723 inline metadata to pull down `pyscard` and `pyserial`.

Run the tool using:

```bash
uv run nexus_deploy.py
```

## Features

- **OMNIKEY PC/SC Mastery**: ISO-7816-4 selection flow, complete EMV extraction (PPSE, AID brute-forcing, BER-TLV parsing with human-readable tags, GPO, and Read Record sweeps), plus memory card probing via the OmniKey Synchronous API.
- **MSR605X Controller**: Serial protocol control for reading and erasing Track 1, 2, and 3 data, alongside a Raw Listener mode for forensic bitstream capture.
- **Luhn Tool**: Validation and check-digit generation for Primary Account Numbers.
- **Advanced TUI**: Asynchronous thread-safe execution, JSON logging to a scrolling buffer with millisecond precision, and real-time background status polling for active hardware endpoints.
