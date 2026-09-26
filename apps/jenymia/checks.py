"""System checks of the jenymia app, run by `manage.py check`."""

from django.core.checks import Error, register


@register()
def product_groups_match_answer_shapes(app_configs, **kwargs) -> list[Error]:
    """Every main category needs both a product group (models) and an answer
    shape (pipeline schema), keyed by the same slug. One without the other
    fails only when the first product of that category is analysed - this
    says so at startup instead."""
    from .models import PRODUCT_GROUPS
    from .pipeline_shared.analysis.schema import ANALYSIS_MODELS

    missing_group = sorted(set(ANALYSIS_MODELS) - set(PRODUCT_GROUPS))
    missing_shape = sorted(set(PRODUCT_GROUPS) - set(ANALYSIS_MODELS))
    errors = []
    if missing_group:
        errors.append(
            Error(
                f"No product group for {missing_group}.",
                hint="Add a class to jenymia/models/product_groups.py.",
                id="jenymia.E001",
            )
        )
    if missing_shape:
        errors.append(
            Error(
                f"No answer shape for {missing_shape}.",
                hint="Add it to ANALYSIS_MODELS in pipeline_shared/analysis/schema/answer.py.",
                id="jenymia.E002",
            )
        )
    return errors
