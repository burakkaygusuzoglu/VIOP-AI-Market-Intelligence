"""Synthesis foundation and decision safety (Phase 7A).

The application half of Phase 7: it turns finished Phase 1-6 results into a
canonical, citable, digestible context, and it decides what a model's proposal
is allowed to become.

    build_synthesis_context   finished results  -> SynthesisContext
    canonical_json / digest   context           -> stable text and hash
    fit_to_budget             context           -> trimmed context, or refusal
    SynthesisOutputSchema     provider JSON     -> structurally valid draft
    validate_synthesis        draft + context   -> permitted, or rejected

No model is called here and no Phase 1-6 formula is recomputed. The action
envelope is a subtraction over results other phases produced; the validator
proves a proposal sits inside it.
"""

# Deliberately no package-level re-exports - see app/domain/synthesis/__init__.py
# for the Phase 6 failure this avoids. Import submodules directly.
