"""Marshmallow request schemas for every /keepa/* route.

ONE param per input (never a sibling `url` / `id` pair):

    product   an ASIN (an ISBN-10 for a book), an Amazon product link or a
              Keepa product link
    country   the Amazon marketplace: us, uk (= gb), de, fr, jp, ca, it, es,
              in, mx, br, au, nl. Default us. When it is absent and
              `product` is a link, the link's own marketplace is used
              (amazon.de, or the "3-" of keepa.com/#!product/3-<ASIN>).

An optional param sent empty (`country=`, `page=`) is the absent param; a
value the site would silently ignore (an unknown sort, a price type it
does not list) is a 400.
"""
from marshmallow import ValidationError, pre_load, validate, validates_schema

from keepa import refs
from schema_fields import (BaseSchema, ChoiceField, CommaListField, Flag, NonNegativeNumber, PageField, PositiveInt,
                           Price, QueryField, RefField, StrippedString)
from scraper_utils.schema import max_length

MAX_HISTORY_DAYS = 36500
_COUNTRY_CHOICES = ", ".join(c.lower() for c in refs.COUNTRIES)


class CountryField(StrippedString):
    """The Amazon marketplace (us, uk / gb, de, …). Default us."""

    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("load_default", refs.DEFAULT_COUNTRY)
        super().__init__(**kwargs)

    def _deserialize(self, value, attr, data, **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        if value is None:
            return refs.DEFAULT_COUNTRY
        try:
            return refs.country(value)
        except ValueError as e:
            raise ValidationError(str(e))


class CountryListField(CommaListField):
    """'us, de,UK' -> ['US', 'DE', 'GB']; None when absent."""

    def _deserialize(self, value, attr, data, **kwargs):
        items = []
        for raw in str(value).split(","):
            if not raw.strip():
                continue
            try:
                code = refs.country(raw)
            except ValueError as e:
                raise ValidationError(f"'{raw.strip()}': {e}")
            if code not in items:
                items.append(code)
        return items or None


class ProductRefField(RefField):
    resolver = staticmethod(refs.resolve_product)


class CategoryListField(CommaListField):
    """'1055398, 172282' -> [1055398, 172282]."""

    def _deserialize(self, value, attr, data, **kwargs):
        items = []
        for raw in str(value).split(","):
            if not raw.strip():
                continue
            try:
                category = refs.resolve_category(raw)
            except ValueError as e:
                raise ValidationError(str(e))
            if category not in items:
                items.append(category)
        if len(items) > self.max_items:
            raise ValidationError(f"At most {self.max_items} values.")
        return items or None


class _Schema(BaseSchema):
    @pre_load
    def empty_is_absent(self, data, **kwargs):
        """An optional param sent empty (a form that sends every field) is
        the absent param: it keeps its default instead of handing a
        validator None. A required param sent empty is still refused."""
        required = {field.data_key or name for name, field in self.fields.items() if field.required}
        return {key: value for key, value in data.items()
                if key in required or not (isinstance(value, str) and not value.strip())}


class _Marketplace(_Schema):
    country = CountryField(metadata={"description": f"Amazon marketplace: {_COUNTRY_CHOICES} (uk = gb); default us"})


class _Product(_Marketplace):
    product = ProductRefField(metadata={
        "description": "ASIN (ISBN-10 for a book), an Amazon product link or a Keepa product link"})

    @pre_load
    def country_from_link(self, data, **kwargs):
        """No `country`, and `product` is a link: the link names the marketplace."""
        data = dict(data)
        if not (isinstance(data.get("country"), str) and data["country"].strip()):
            named = refs.country_of(data.get("product")) if isinstance(data.get("product"), str) else None
            if named:
                data["country"] = named
        return data


class ProductDetailsSchema(_Product):
    refresh = Flag(load_default=False, metadata={
        "description": "true = Keepa reads the product from Amazon again before it answers (4 to 12 seconds)"})
    include_buy_box = Flag(load_default=False, metadata={
        "description": "true = add the buy box (price, seller, Prime, FBA), offer counts, stock, sales-rank drops, coupons and deal badges"})


class PriceHistorySchema(_Product):
    price_types = CommaListField(allowed=refs.PRICE_TYPES, upper=False, max_items=len(refs.PRICE_TYPES), metadata={
        "description": "comma list of the series to return (amazon, new, used, buy_box, list_price, new_fba, new_fbm, warehouse, "
                       "lightning_deal, rating, …); default every series the product has"})
    days = PositiveInt(max_value=MAX_HISTORY_DAYS, metadata={
        "description": "only the last N days of each series (plus the value the window opens with); default the full history"})


class ProductSchema(_Product):
    pass


class InternationalPricesSchema(_Schema):
    product = ProductRefField(metadata={
        "description": "ASIN (ISBN-10 for a book), an Amazon product link or a Keepa product link"})
    countries = CountryListField(max_items=len(refs.COUNTRIES), metadata={
        "description": f"comma list of marketplaces to compare ({_COUNTRY_CHOICES}); default all of them"})


class ProductsSearchSchema(_Marketplace):
    query = QueryField(metadata={"description": "keywords, an ASIN, or a UPC / EAN"})


class CategoriesSearchSchema(_Marketplace):
    query = QueryField(max_length=100, metadata={"description": "part of a category name, e.g. kitchen"})


class DealsListSchema(_Marketplace):
    page = PageField(max_page=refs.DEALS_MAX_PAGE, metadata={
        "description": f"results page, {refs.DEALS_PER_PAGE} deals each; the site serves the first 10,000 deals ({refs.DEALS_MAX_PAGE} pages)"})
    price_type = ChoiceField(list(refs.DEAL_PRICE_TYPES), metadata={
        "description": "the price that dropped: " + ", ".join(refs.DEAL_PRICE_TYPES) + "; default amazon (new on amazon.in, as on the site)"})
    drop_interval = ChoiceField(list(refs.DEAL_INTERVALS), load_default="week", metadata={
        "description": "the drop is measured against the average of the last day, week, month, 3_months, or all of them; default week"})
    sort = ChoiceField(list(refs.DEAL_SORTS), load_default="percent", metadata={
        "description": "percent (biggest percentage drop first), drop (biggest amount), price (cheapest), newest, sales_rank; default percent"})
    order = ChoiceField(list(refs.DEAL_ORDERS), metadata={
        "description": "asc or desc; default the sort's own (percent / drop / newest: desc, price / sales_rank: asc)"})
    query = StrippedString(load_default=None, validate=max_length(200), metadata={
        "description": "only deals whose title has these words"})
    categories = CategoryListField(max_items=50, metadata={
        "description": "comma list of category ids to keep (the `id` of /keepa/categories/search rows or of this answer's `categories`)"})
    exclude_categories = CategoryListField(max_items=50, metadata={"description": "comma list of category ids to leave out"})
    min_drop_percent = NonNegativeNumber(validate=validate.Range(min=0, max=100), metadata={"description": "smallest drop in percent (0-100)"})
    max_drop_percent = NonNegativeNumber(validate=validate.Range(min=0, max=100), metadata={"description": "largest drop in percent (0-100)"})
    min_drop = Price(metadata={"description": "smallest drop as an amount in the marketplace's currency"})
    max_drop = Price(metadata={"description": "largest drop as an amount in the marketplace's currency"})
    min_price = Price(metadata={"description": "lowest current price, in the marketplace's currency"})
    max_price = Price(metadata={"description": "highest current price, in the marketplace's currency"})
    min_rating = NonNegativeNumber(validate=validate.Range(min=1, max=5), metadata={"description": "lowest star rating, 1 to 5 (4.5 allowed)"})
    lowest_ever = Flag(load_default=False, metadata={"description": "true = only prices at their lowest ever"})
    lowest_90_days = Flag(load_default=False, metadata={"description": "true = only prices at their lowest of 90 days"})
    lowest_new_offer = Flag(load_default=False, metadata={"description": "true = only when this price is the lowest of all new offers"})
    out_of_stock = Flag(load_default=False, metadata={"description": "true = only products that went out of stock"})
    has_reviews = Flag(load_default=False, metadata={"description": "true = only products with reviews"})
    prime_exclusive = Flag(load_default=False, metadata={"description": "true = only Prime-exclusive prices (price_type=amazon)"})
    amazon_offer = ChoiceField(["with", "without"], metadata={
        "description": "with = only products Amazon itself sells, without = only products it does not"})
    include_adult = Flag(load_default=False, metadata={"description": "true = adult items are not filtered out"})
    all_variations = Flag(load_default=False, metadata={"description": "true = every variation of a product, not one per product"})
    warehouse_conditions = CommaListField(value_map=refs.WAREHOUSE_CONDITIONS, upper=False, max_items=5, metadata={
        "description": "price_type=warehouse only: comma list of new, like_new, very_good, good, acceptable; default all"})

    @validates_schema
    def _consistent(self, data, **kwargs):
        for low, high in (("min_drop_percent", "max_drop_percent"), ("min_drop", "max_drop"), ("min_price", "max_price")):
            if data.get(low) is not None and data.get(high) is not None and data[low] > data[high]:
                raise ValidationError(f"Must not be below {low}.", high)
        if data.get("order"):
            try:
                refs.deal_sort_type(data["sort"], data["order"])
            except ValueError as e:
                raise ValidationError(str(e), "order")
        if data.get("warehouse_conditions") and data.get("price_type") != "warehouse":
            raise ValidationError("Only with price_type=warehouse.", "warehouse_conditions")
        # the type an absent price_type means (api._deals): new on amazon.in, amazon elsewhere
        default_type = "new" if data.get("country") == "IN" else "amazon"
        if data.get("prime_exclusive") and (data.get("price_type") or default_type) != "amazon":
            raise ValidationError("Only with price_type=amazon.", "prime_exclusive")
