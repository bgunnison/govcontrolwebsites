LAST_DATE = '1/01/2026'
PROMPT_GUIDE = 'Please research distinct new AI items published after [LAST_DATE] that fit [TOPIC]. Find as many worthwhile source-backed items as you can, and treat each item as a separate future website post. Prefer primary sources when they are relevant.'


WEBSITE_TOPICS = [
    {
        'Topic': 'Politics',
        'Prompt': '[PROMPT_GUIDE]. For the topic [TOPIC] find federal state and local concerns and speeches.',
        'LastDate': '09/14/2026',
    },
    {
        'Topic': 'Legislation',
        'Prompt': '[PROMPT_GUIDE]. For the topic [TOPIC] find proposed laws and how these will influence AI vendors',
        'LastDate': '09/14/2026',
    },
    {
        'Topic': 'Law',
        'Prompt': '[PROMPT_GUIDE]. For the topic [TOPIC] find laws passed and who authored them and any controversy. Data centers for example',
        'LastDate': '09/14/2026',
    },
    {
        'Topic': 'Risk',
        'Prompt': '[PROMPT_GUIDE]. For the topic [TOPIC] find proposed guardrails, existential risk articles and actual harm caused',
        'LastDate': '09/14/2026',
    },
    {
        'Topic': 'Privacy',
        'Prompt': '[PROMPT_GUIDE]. For the topic [TOPIC] find media illustrating the danger of AI data gathering and how it can be used. Other examples are deep fakes. ',
        'LastDate': '09/14/2026',
    },
    {
        'Topic': 'Ethics',
        'Prompt': '[PROMPT_GUIDE]. For the topic [TOPIC] find article, papaers and vidoes discussing morality, philosophy and values.  ',
        'LastDate': '09/14/2026',
    },
    {
        'Topic': 'Culture',
        'Prompt': '[PROMPT_GUIDE]. Find media representsing the current zeitgeist, include humor, celebrity media, movies etc.   ',
        'LastDate': '09/14/2026',
    },
    {
        'Topic': 'Activism',
        'Prompt': '[PROMPT_GUIDE]. Find media showing any protests, environmental concerns and popular opinions for or against.    ',
        'LastDate': '09/14/2026',
    },
    {
        'Topic': 'Future',
        'Prompt': '[PROMPT_GUIDE]. Focus on state-of-the-art technical articles and official videos from major AI labs and infrastructure vendors such as OpenAI, Anthropic, Google, DeepMind, Microsoft, NVIDIA, Meta, Amazon, xAI, and leading robotics or chip companies. Prefer official release posts, technical explainers, research writeups, keynote videos, model launches, agent platforms, video platforms, robotics updates, and infrastructure announcements. Exclude generic smartphone, CES, or gadget roundup coverage unless it is the primary official source for a major frontier AI release.',
        'LastDate': '09/14/2026',
    },
]
