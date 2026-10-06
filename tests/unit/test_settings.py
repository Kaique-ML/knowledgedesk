import pytest
from pydantic import ValidationError

from knowledgedesk.settings import Settings


def test_empty_optional_model_environment_values_are_none() -> None:
    settings = Settings(
        embedding_model="",
        generation_model="",
        embedding_dimensions="",  # type: ignore[arg-type]
    )

    assert settings.embedding_model is None
    assert settings.generation_model is None
    assert settings.embedding_dimensions is None


def test_deployment_requires_secret_and_secure_cookies() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="deployment", secure_cookies=False)
