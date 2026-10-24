from pathlib import Path

import pytest

from m_dlp import cli

MAGNET = "magnet:?xt=urn:btih:" + "a" * 40


def parse(*args):
    return cli._parse_args([MAGNET, "--list", *args])


def test_limits_accept_size_units_and_zero():
    args = cli._parser().parse_args([MAGNET, "--download-limit", "1.5M", "--upload-limit", "0"])
    assert args.download_limit == 1572864
    assert args.upload_limit == 0


def test_network_interface_default_and_config_precedence(tmp_path, monkeypatch):
    assert parse().network_interface is None
    config = tmp_path / "interface.toml"
    config.write_text('network-interface = "utun8"\n')
    assert parse("--config", str(config)).network_interface == "utun8"
    assert (
        parse("--config", str(config), "--network-interface", "utun9").network_interface == "utun9"
    )
    monkeypatch.setenv("M_DLP_CONFIG", str(config))
    assert parse("--no-config").network_interface is None


@pytest.mark.parametrize("value", ["", " ", "utun8,en0", "utun8:0", "utun8\x00", "utun8\n"])
def test_invalid_network_interface_cli_fails(value, capsys):
    with pytest.raises(SystemExit) as exc:
        parse("--network-interface", value)
    assert exc.value.code == 2
    error = capsys.readouterr().err
    assert "--network-interface" in error
    assert "unrecognized arguments" not in error


@pytest.mark.parametrize("value", ['""', '"utun8,en0"', '"utun8:0"', "42", "1.5", "true", "[]"])
def test_invalid_network_interface_config_fails(tmp_path, value, capsys):
    config = tmp_path / "interface.toml"
    config.write_text(f"network-interface = {value}\n")
    with pytest.raises(SystemExit) as exc:
        parse("--config", str(config))
    assert exc.value.code == 2
    error = capsys.readouterr().err
    assert "interface.toml" in error
    assert "不支持的配置项" not in error


@pytest.mark.parametrize("mode", ["single", "batch", "queue-selection", "queue-run"])
def test_missing_network_interface_stops_cli_before_network(tmp_path, monkeypatch, capsys, mode):
    from m_dlp import engine

    interface = "m-dlp-interface-that-does-not-exist"

    def no_session(*args, **kwargs):
        pytest.fail("An unavailable bound interface must fail before creating network sockets")

    monkeypatch.setattr(engine.lt, "session", no_session)
    binding = ["--network-interface", interface]
    if mode == "single":
        argv = [MAGNET, "--list", *binding]
    elif mode == "batch":
        batch = tmp_path / "magnets.txt"
        batch.write_text(MAGNET + "\n")
        argv = ["--batch-file", str(batch), "--list", *binding]
    elif mode == "queue-selection":
        monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
        argv = ["queue", "add", MAGNET, "--interactive", *binding]
    else:
        assert cli.run(["queue", "add", MAGNET, "--all", *binding]) == 0
        argv = ["queue", "run"]
    assert cli.run(argv) == 1
    assert interface in capsys.readouterr().err


@pytest.mark.parametrize("value", ["-1", "nan", "2G", "0.1B", "1Mbps"])
def test_invalid_limits_fail(value):
    with pytest.raises(SystemExit) as exc:
        cli._parser().parse_args([MAGNET, "--download-limit", value])
    assert exc.value.code == 2


def test_config_precedence_and_relative_paths(tmp_path):
    config = tmp_path / "settings.toml"
    config.write_text(
        'output = "downloads"\ndownload-limit = "2M"\nupload-limit = "500K"\n'
        "metadata-timeout = 20\nstall-timeout = 0\nprobe-seconds = 3.5\n"
        'cache-dir = "state"\nno-cache = true\n'
    )
    args = parse("--config", str(config), "--download-limit", "0", "--cache")
    assert args.output == tmp_path / "downloads"
    assert args.cache_dir == tmp_path / "state"
    assert args.download_limit == 0
    assert args.upload_limit == 512000
    assert args.metadata_timeout == 20
    assert args.stall_timeout == 0
    assert args.probe_seconds == 3.5
    assert not args.no_cache
    args = parse("--config", str(config), "-o", "cli-dir")
    assert args.output == Path("cli-dir")


def test_default_and_env_config(tmp_path, monkeypatch):
    default = tmp_path / "config-home/m-dlp/config.toml"
    default.parent.mkdir(parents=True)
    default.write_text('upload-limit = "20K"\n')
    assert parse().upload_limit == 20480
    alternate = tmp_path / "alternate.toml"
    alternate.write_text('upload-limit = "30K"\n')
    monkeypatch.setenv("M_DLP_CONFIG", str(alternate))
    assert parse().upload_limit == 30720
    assert parse("--config", str(default)).upload_limit == 20480
    assert parse("--no-config").upload_limit == 0


@pytest.mark.parametrize(
    "content",
    [
        "download-limit = -1",
        "download-limit = true",
        "metadata-timeout = 0",
        "stall-timeout = nan",
        'upload-limit = "2G"',
        "output = 42",
        'output = ""',
        'no-cache = "yes"',
        "typo = 1",
        "[defaults]\nupload-limit = 1",
        "output = [",
    ],
)
def test_bad_config_fails_clearly(tmp_path, content, capsys):
    config = tmp_path / "bad.toml"
    config.write_text(content)
    with pytest.raises(SystemExit) as exc:
        parse("--config", str(config))
    assert exc.value.code == 2
    assert "bad.toml" in capsys.readouterr().err


def test_missing_explicit_config_is_error_but_can_be_disabled(tmp_path, monkeypatch):
    config = tmp_path / "missing.toml"
    with pytest.raises(SystemExit):
        parse("--config", str(config))
    monkeypatch.setenv("M_DLP_CONFIG", str(config))
    with pytest.raises(SystemExit):
        parse()
    assert parse("--no-config").download_limit == 0


def test_help_and_version_ignore_bad_config(tmp_path, monkeypatch):
    bad = tmp_path / "bad.toml"
    bad.write_text("bad [")
    monkeypatch.setenv("M_DLP_CONFIG", str(bad))
    for option in ("--help", "--version"):
        with pytest.raises(SystemExit) as exc:
            cli._parse_args([option])
        assert exc.value.code == 0


def test_unexpandable_config_location_is_clean_error(capsys):
    with pytest.raises(SystemExit) as exc:
        parse("--config", "~m_dlp_nonexistent_834374/config.toml")
    assert exc.value.code == 2
    assert "配置文件" in capsys.readouterr().err


@pytest.mark.parametrize("key", ["output", "cache-dir"])
@pytest.mark.parametrize("value", ["~m_dlp_nonexistent_834374/out", r"bad\u0000path"])
def test_invalid_config_paths_fail_before_network(tmp_path, key, value, capsys):
    config = tmp_path / "invalid-path.toml"
    config.write_text(f'{key} = "{value}"\n')
    with pytest.raises(SystemExit) as exc:
        parse("--config", str(config))
    assert exc.value.code == 2
    assert "invalid-path.toml" in capsys.readouterr().err
