"""Dynamic plugin loading for screening providers."""

import importlib

from config import (
    DELIVERY_CHANNEL_PLUGIN,
    EVIDENCE_REVIEWER_PLUGIN,
    IMAGE_EXTRACTOR_PLUGIN,
    SCREENING_PROVIDER_PLUGINS,
)
from image_extractors import ImageExtractor
from scrapers.base import BaseScraper


def load_screening_providers(
    plugin_paths: list[str] | None = None,
) -> list[BaseScraper]:
    """Instantiate configured `module:Class` screening providers."""
    providers: list[BaseScraper] = []
    for plugin_path in plugin_paths or SCREENING_PROVIDER_PLUGINS:
        try:
            module_name, class_name = plugin_path.split(":", 1)
        except ValueError as exc:
            raise ValueError(
                f"Invalid provider plugin {plugin_path!r}; expected module:Class"
            ) from exc
        module = importlib.import_module(module_name)
        provider_class = getattr(module, class_name)
        provider = provider_class()
        if not isinstance(provider, BaseScraper):
            raise TypeError(f"Provider {plugin_path!r} must extend BaseScraper")
        providers.append(provider)

    source_names = [provider.source_name for provider in providers]
    if len(source_names) != len(set(source_names)):
        raise ValueError("Configured provider source names must be unique")
    return providers


def load_evidence_reviewer(plugin_path: str | None = None):
    """Load the optional evidence reviewer through the same plugin mechanism."""
    path = EVIDENCE_REVIEWER_PLUGIN if plugin_path is None else plugin_path
    if not path:
        return None
    module_name, class_name = path.split(":", 1)
    return getattr(importlib.import_module(module_name), class_name)()


def load_delivery_channel(plugin_path: str | None = None):
    """Load the configured user-facing transport."""
    path = DELIVERY_CHANNEL_PLUGIN if plugin_path is None else plugin_path
    module_name, class_name = path.split(":", 1)
    return getattr(importlib.import_module(module_name), class_name)()


def load_image_extractor(image_cache=None, plugin_path: str | None = None):
    """Load the configured image ticker extractor."""
    path = IMAGE_EXTRACTOR_PLUGIN if plugin_path is None else plugin_path
    if not path:
        return None
    module_name, class_name = path.split(":", 1)
    extractor = getattr(importlib.import_module(module_name), class_name)(image_cache=image_cache)
    if not isinstance(extractor, ImageExtractor):
        raise TypeError(f"Image extractor {path!r} must extend ImageExtractor")
    return extractor
