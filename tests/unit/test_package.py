def test_release_version():
    from comms.transports.telegram import __version__

    assert __version__ == "0.1.10"
