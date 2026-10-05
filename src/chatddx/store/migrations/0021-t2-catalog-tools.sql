-- src/chatddx/core/catalog.py: THREAD_KINDS
ALTER TABLE catalog.thread
    DROP CONSTRAINT thread_kind_check,
    ADD CONSTRAINT thread_kind_check CHECK (kind IN (
        'chunk.instructions', 'chunk.few_shot', 'chunk.prompt', 'chunk.output',
        'chunk.sampling', 'chunk.reasoning', 'chunk.passthrough', 'chunk.translations',
        'chunk.toolset', 'tool', 'skeleton', 'trial', 'judge', 'scorer', 'scoring',
        'appendix', 'expectation', 'model', 'engine.local', 'engine.remote',
        'expectation_schema', 'canary_set'
    ));
