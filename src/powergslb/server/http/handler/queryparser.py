""" QueryString parser

QueryString parser for Python/Django that correctly handles nested dictionaries
https://github.com/bernii/querystring-parser
"""

from urlparse import parse_qsl

__all__ = ['QueryParserError', 'parse_query']


class QueryParserError(Exception):
    """
    Query string is malformed
    """
    pass


def _get_key(s):
    """
    Get data between [ and ], remove ' if exist
    @param s: string
    """
    start = s.find('[')
    end = s.find(']')
    if start == -1 or end == -1:
        return None
    if s[start + 1] == "'":
        start += 1
    if s[end - 1] == "'":
        end -= 1
    return s[start + 1:end]  # without brackets


def _has_variable_name(s):
    """
    Variable name before [
    @param s: string
    """
    return s.find('[') > 0


def _is_number(s):
    """
    Check if s is an int (for indexes in dict)
    @param s: string
    """
    if len(s) > 0 and s[0] in ('-', '+'):
        return s[1:].isdigit()
    return s.isdigit()


def _more_than_one_index(s, brackets=2):
    """
    Search for two sets of [] []
    @param s: string
    @param brackets: int
    """
    start = 0
    brackets_num = 0
    while start != -1 and brackets_num < brackets:
        start = s.find('[', start)
        if start == -1:
            break
        start = s.find(']', start)
        brackets_num += 1
    if start != -1:
        return True
    return False


def _normalize(d):
    """
    The parse() function generates output of list in dict form
    i.e. {'abc' : {0: 'xyz', 1: 'pqr'}}. This function normalize it and turn
    them into proper data type, i.e. {'abc': ['xyz', 'pqr']}
    Note: if dict has element starts with 10, 11 etc.. this function won't fill
    blanks, for eg: {'abc': {10: 'xyz', 12: 'pqr'}} will convert to
    {'abc': ['xyz', 'pqr']}
    @param d: dict
    """
    newd = {}
    if not isinstance(d, dict):
        return d
    # if dictionary. iterate over each element and append to newd
    for k, v in d.iteritems():
        if isinstance(v, dict):
            first_key = next(iter(v.viewkeys()))
            if isinstance(first_key, int):
                temp_new = []
                for k1, v1 in v.items():
                    temp_new.append(_normalize(v1))
                newd[k] = temp_new
            elif first_key == '':
                newd[k] = v.values()[0]
            else:
                newd[k] = _normalize(v)
        else:
            newd[k] = v
    return newd


def _parser_helper(key, val):
    """
    Helper for parser function
    @param key:
    @param val:
    """
    start_bracket = key.find('[')
    end_bracket = key.find(']')
    pdict = {}
    if _has_variable_name(key):  # var['key'][3]
        pdict[key[:start_bracket]] = _parser_helper(key[start_bracket:], val)
    elif _more_than_one_index(key):  # ['key'][3]
        newkey = _get_key(key)
        newkey = int(newkey) if _is_number(newkey) else newkey
        pdict[newkey] = _parser_helper(key[end_bracket + 1:], val)
    else:  # key = val or ['key']
        newkey = key
        if start_bracket != -1:  # ['key']
            newkey = _get_key(key)
            if newkey is None:
                raise QueryParserError
        newkey = int(newkey) if _is_number(newkey) else newkey
        if key == u'[]':  # val is the array key
            val = int(val) if _is_number(val) else val
        pdict[newkey] = val
    return pdict


def parse_query(query_string):
    """
    Main parse function
    http://www.w3.org/TR/html5/forms.html#application/x-www-form-urlencoded-encoding-algorithm
    @param query_string: str
    """
    query_dict = {}
    if not query_string:
        return query_dict
    piter = (_parser_helper(key, val) for key, val in parse_qsl(query_string))
    for di in piter:
        k, v = di.popitem()
        tempdict = query_dict
        while k in tempdict and type(v) is dict:
            tempdict = tempdict[k]
            k, v = v.popitem()
        if k in tempdict and type(tempdict[k]).__name__ == 'list':
            tempdict[k].append(v)
        elif k in tempdict:
            tempdict[k] = [tempdict[k], v]
        else:
            tempdict[k] = v
    return _normalize(query_dict)
