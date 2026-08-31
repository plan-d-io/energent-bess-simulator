# Energent BESS Simulator

<p align="left">
  <a href="https://energent.be/"><img src="ui/assets/Energent.png" alt="Energent" width="180"></a>
</p>

An analysis tool for behind-the-meter
battery projects. It reads quarter-hourly Fluvius CSV exports, compares six
dispatch cases for one battery, or it can screen a range of battery sizes.

The application is intended for expert users. Its results are estimates under
the configured assumptions. They are not operational forecasts, customer bill
calculations, vendor quotations, profit, net present value, or a complete
business case.

## Quick start (for Windows users)

First, make sure you have **Python 3.13** installed. We suggest getting it from the Microsoft Store.

Then:
1. Clone this repository, or download and extract its ZIP file (`<> Code` button in the upper right corner > `Download ZIP`) .
2. Extract the contents of the ZIP to a folder on your disk with write access.
3. Double-click `setup.cmd`. It creates a private `.venv` and installs the
   tested dependencies. This might take a while. Make sure to allow it internet access.
4. When the setup script has finished, double-click `start.cmd`.
5. Open the local address shown in the terminal if the browser does not open
   automatically.

For any use after this, just use `start.cmd` again.

## Try the saved demonstration

Enable **Demo mode** on the first page if you want discover the tool without running a simulation. You can walk through both
the one-battery comparison and battery-size screening results.

## Run your own analysis

Upload these three Fluvius CSV exports together:

- grid offtake;
- grid injection;
- PV production from the PV submeter.

The application detects the meter roles from the file contents. It validates
the common period, asks for any necessary acknowledgements, and shows the
effective settings before a worker starts. Progress and diagnostic output stay
visible while the run is active.

New result folders are written under `outputs/`.

The dynamic-injection case uses the bundled Belgian day-ahead dataset in
`data/market/`.

## Defaults and assumptions

Edit `configs/defaults.toml` to change the starting values used by future runs.
The UI lets you override these values for one run without changing the file.
Completed result folders retain their resolved configuration and audit data.

The main assumptions and boundaries are documented in:

- [Scope](docs/SCOPE.md)
- [Model specification](docs/MODEL_SPEC.md)
- [Data contract](docs/DATA_CONTRACT.md)
- [Configuration](docs/CONFIGURATION.md)
- [Solver backend](docs/SOLVER.md)
- [Battery-size sweep](docs/SWEEP.md)

## Command-line use

Activate the environment from PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Then inspect the supported commands:

```powershell
btm-run --help
btm-sweep --help
btm-compare --help
```

Run the installation check at any time with:

```powershell
.\.venv\Scripts\python.exe scripts\doctor.py
```

## Development

Install the test dependency and run the public test suites:

```powershell
.\.venv\Scripts\python.exe -m pip install "pytest>=8"
.\.venv\Scripts\python.exe -m pytest tests ui\tests -q
```

The production optimizer is HiGHS.

## Licence

This project is distributed under the
[PolyForm Noncommercial License 1.0.0](LICENSE.md). Noncommercial use,
modification, and distribution are permitted under those terms. Commercial use
requires separate permission from the licensor.

## Made by

Made by [Plan-D.io](https://www.plan-d.io/), for [Energent cvba](https://energent.be/). See [AUTHORS.md](https://github.com/plan-d-io/energent-bess-simulator/blob/main/AUTHORS.md).

<p align="right">
  <a href="https://www.plan-d.io/"><img src="ui/assets/Plan%20D-small-transparent.png" alt="Plan-D" width="200"></a>
</p>
