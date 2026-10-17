import pytest

from python.evaluation.paper_market import DataError, entry_limit, fee_rate, final_outcome, get_book, load_hold_market


class Response:
    def __init__(self, data): self.data = data
    def raise_for_status(self): pass
    def json(self): return self.data


class Client:
    def __init__(self, data): self.data = data
    def get(self, *args, **kwargs): return Response(self.data)


def market():
    return {"id":"1", "conditionId":"cond", "question":"Will there be no change in Fed interest rates after the October 2026 meeting?", "active":True, "closed":False, "enableOrderBook":True, "acceptingOrders":True, "outcomes":["No","Yes"], "clobTokenIds":["10","20"], "feesEnabled":True, "feeSchedule":{"exponent":1,"rate":0.05,"takerOnly":True}}


def test_yes_token_follows_label_not_index():
    result=load_hold_market("event", "2026-10-28",session=Client([{"id":"e","slug":"event","markets":[market()]}]))
    assert result["tokens_by_side"] == {"NO":"10","YES":"20"}


def test_wrong_month_cannot_enter_universe():
    with pytest.raises(DataError, match="grammar"):
        load_hold_market("event", "2026-12-09",session=Client([{"id":"e","slug":"event","markets":[market()]}]))


def test_fee_unknown_is_not_free():
    with pytest.raises(DataError): fee_rate({})
    assert fee_rate({"feesEnabled":False}) == 0


def test_entry_limit_reserves_fee_and_uncertainty():
    p=entry_limit(0.60,0.05,0.03)
    assert p + 0.05*p*(1-p) == pytest.approx(0.57,abs=1e-7)


def book():
    return {"market":"cond","asset_id":"20","timestamp":"1000000","min_order_size":"5","bids":[{"price":"0.5","size":"10"}],"asks":[{"price":"0.6","size":"20"}]}


def test_sort_and_validate_book():
    x=book(); x["asks"].append({"price":"0.55","size":"3"})
    assert get_book("20","cond",now=1001,session=Client(x))["asks"][0] == (0.55,3)


@pytest.mark.parametrize("change", [{"timestamp":"900000"},{"asset_id":"wrong"},{"asks":[{"price":"nan","size":"4"}]}])
def test_bad_quotes_cannot_fill(change):
    x=book(); x.update(change)
    with pytest.raises(DataError): get_book("20","cond",now=1001,session=Client(x))


def test_extreme_price_is_not_final_outcome():
    x={"condition_id":"cond","closed":False,"tokens":[{"outcome":"Yes","price":1,"winner":True},{"outcome":"No","price":0,"winner":False}]}
    assert final_outcome("cond",session=Client(x)) is None
    x["closed"]=True
    assert final_outcome("cond",session=Client(x)) == "YES"
    x["tokens"][0]["winner"]=False
    with pytest.raises(DataError, match="manual_resolution"):
        final_outcome("cond",session=Client(x))
