FINAL = frozenset(('a', 'b', 'reject'))


def grade(rule, record, edge, repeated):
    minimum = rule['margin_min'] + (rule['edge_add'] if edge else 0)
    premium = rule['grade_a'] + (rule['edge_add'] if edge else 0)
    leak, margin = record['leak_na'], record['margin_mv']
    if record['contact'] == 'open':
        state, why = 'review', '接触开路，无有效测值'
    elif leak > rule['leak_limit']:
        state, why = 'reject', '漏电超过上限'
    elif not repeated and rule['guard'] and abs(margin - minimum) <= rule['guard']:
        state, why = 'review', '裕量处于首测复测带'
    elif margin < minimum:
        state, why = 'reject', '裕量低于适用下限'
    elif margin >= premium:
        state, why = 'a', '满足甲档裕量及漏电要求'
    else:
        state, why = 'b', '满足乙档裕量及漏电要求'
    return {'state': state, 'why': why, 'margin_min': minimum, 'grade_a': premium}
