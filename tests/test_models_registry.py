from sqlalchemy.orm import configure_mappers


def test_all_sqlalchemy_mappers_configure():
    """Every model must be registered so string relationships resolve."""
    import app.models  # noqa: F401

    configure_mappers()
