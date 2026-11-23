from models import db, ProductProfile
from etsy_api import ListingSyncManager, EtsyAPIError


class FakeEtsyAPI:
    def __init__(self, listings, raise_status=None):
        self.listings = listings
        self.raise_status = raise_status

    def get_shop_listings(self, shop_id, **params):
        if self.raise_status:
            raise EtsyAPIError('boom', status_code=self.raise_status)
        if params.get('offset', 0) > 0:
            return {'results': [], 'count': len(self.listings)}
        return {'results': self.listings, 'count': len(self.listings)}


def make_listing(listing_id=999, title='Cool Benchy', price_cents=1500):
    return {
        'listing_id': listing_id,
        'title': title,
        'description': 'A cool benchy',
        'price': {'amount': price_cents, 'divisor': 100, 'currency_code': 'USD'},
        'quantity': 10,
        'state': 'active',
        'url': f'https://etsy.com/listing/{listing_id}',
        'images': [{'url_170x135': 'https://img/thumb.jpg'}]
    }


def test_sync_creates_new_product_profile_from_listing(app, user):
    fake = FakeEtsyAPI([make_listing()])
    result = ListingSyncManager.sync_listings_from_etsy(user, 'shop1', fake)

    assert result['success'] is True
    assert result['new_products_created'] == 1
    profile = ProductProfile.query.filter_by(etsy_listing_id='999').first()
    assert profile is not None
    assert profile.product_name == 'Cool Benchy'
    assert profile.etsy_price == 15.0


def test_sync_does_not_clobber_hand_edited_profile(app, user):
    manual = ProductProfile(user_id=user.id, product_name='Manual Widget', standard_filament_amount=25)
    db.session.add(manual)
    db.session.commit()

    fake = FakeEtsyAPI([make_listing()])
    ListingSyncManager.sync_listings_from_etsy(user, 'shop1', fake)

    reloaded = db.session.get(ProductProfile, manual.id)
    assert reloaded.product_name == 'Manual Widget'
    assert reloaded.standard_filament_amount == 25


def test_second_sync_updates_rather_than_duplicates(app, user):
    fake = FakeEtsyAPI([make_listing()])
    ListingSyncManager.sync_listings_from_etsy(user, 'shop1', fake)
    result2 = ListingSyncManager.sync_listings_from_etsy(user, 'shop1', fake)

    assert result2['new_products_created'] == 0
    assert result2['updated_products'] == 1
    assert ProductProfile.query.count() == 1


def test_resync_does_not_revert_manual_rename_or_description_edit(app, user):
    fake = FakeEtsyAPI([make_listing(title='Original Etsy Title')])
    ListingSyncManager.sync_listings_from_etsy(user, 'shop1', fake)

    profile = ProductProfile.query.filter_by(etsy_listing_id='999').first()
    profile.product_name = 'My Renamed Product'
    profile.description = 'My own rewritten description'
    db.session.commit()

    # Etsy's title/description "changed" on their side, but our copy is user-owned now
    fake2 = FakeEtsyAPI([make_listing(title='Updated Etsy Title')])
    ListingSyncManager.sync_listings_from_etsy(user, 'shop1', fake2)

    reloaded = ProductProfile.query.filter_by(etsy_listing_id='999').first()
    assert reloaded.product_name == 'My Renamed Product'
    assert reloaded.description == 'My own rewritten description'
    # Etsy-owned fields still refresh normally
    assert reloaded.etsy_price == 15.0


def test_sync_flags_needs_reconnect_on_403(app, user):
    fake = FakeEtsyAPI([], raise_status=403)
    result = ListingSyncManager.sync_listings_from_etsy(user, 'shop1', fake)

    assert result['success'] is False
    assert result['needs_reconnect'] is True


def test_sync_decodes_html_entities_in_title_and_description(app, user):
    listing = make_listing(title="St Patrick&#39;s Day Exclusive")
    listing['description'] = "Comes with &amp; without a stand"
    fake = FakeEtsyAPI([listing])

    ListingSyncManager.sync_listings_from_etsy(user, 'shop1', fake)

    profile = ProductProfile.query.filter_by(etsy_listing_id='999').first()
    assert profile.product_name == "St Patrick's Day Exclusive"
    assert profile.description == "Comes with & without a stand"
