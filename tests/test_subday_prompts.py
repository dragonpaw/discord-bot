import hikari

from dragonpaw_bot.plugins.subday import prompts


def test_load_week_1():
    p = prompts.load_week(1)
    assert p.week == 1
    assert p.guidepost_title == "Determination"
    assert "Determination can often" in p.guidepost_body
    assert "giving up control" in p.thought_1
    assert "strong power" in p.thought_2
    assert p.assignment_title == "Role Models"
    assert "fictional characters" in p.assignment_body


def test_load_week_13_milestone():
    p = prompts.load_week(13)
    assert p.week == 13
    assert p.guidepost_title == "Commitment"
    assert p.assignment_title == "More Fantasy Exploration"
    # Milestone congratulations line should be stripped
    assert "Congra" not in p.assignment_body


def test_load_week_52():
    p = prompts.load_week(52)
    assert p.week == 52
    assert p.guidepost_title == "Guideposts"
    assert p.assignment_title == "Looking Back"


def test_load_all_52_weeks():
    for n in range(1, 53):
        p = prompts.load_week(n)
        assert p.week == n
        assert p.guidepost_title, f"Week {n}: missing guidepost title"
        assert p.guidepost_body, f"Week {n}: missing guidepost body"
        assert p.thought_1, f"Week {n}: missing thought 1"
        assert p.thought_2, f"Week {n}: missing thought 2"
        assert p.assignment_title, f"Week {n}: missing assignment title"


def test_build_prompt_embed():
    p = prompts.load_week(1)
    embed = prompts.build_prompt_embed(p)
    assert isinstance(embed, hikari.Embed)
    assert "Week 1" in embed.title
    assert len(embed.fields) == 4


def test_caching():
    # Clear cache
    prompts._cache.clear()
    p1 = prompts.load_week(1)
    p2 = prompts.load_week(1)
    assert p1 is p2


def test_load_rules():
    rules = prompts.load_rules()
    assert "Guidepost" in rules
    assert "Owner" in rules
