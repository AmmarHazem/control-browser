import pytest

from app.policy.limits import clamp_limit
from app.policy.network import (
    DomainPolicy,
    is_blocked_address,
    matches_domain,
    parse_target,
)
from app.policy.risky import is_submit_control, matches_risky_url


class TestDomainMatching:
    def test_bare_pattern_matches_the_host_itself(self):
        assert matches_domain("example.com", "example.com")

    def test_bare_pattern_matches_subdomains(self):
        assert matches_domain("shop.example.com", "example.com")

    def test_bare_pattern_does_not_match_a_lookalike_suffix(self):
        assert not matches_domain("notexample.com", "example.com")

    def test_wildcard_pattern_matches_subdomains_only(self):
        assert matches_domain("shop.example.com", "*.example.com")
        assert not matches_domain("example.com", "*.example.com")

    def test_matching_ignores_case(self):
        assert matches_domain("SHOP.Example.COM", "example.com")


class TestAddressClassification:
    @pytest.mark.parametrize(
        "address",
        [
            "127.0.0.1",
            "10.1.2.3",
            "192.168.0.5",
            "172.16.4.4",
            "169.254.169.254",
            "0.0.0.0",
            "::1",
            "fc00::1",
            "fe80::1",
            "224.0.0.1",
        ],
    )
    def test_internal_addresses_are_blocked(self, address):
        assert is_blocked_address(address) is True

    @pytest.mark.parametrize("address", ["93.184.216.34", "8.8.8.8", "2606:2800:220:1::1"])
    def test_public_addresses_are_allowed(self, address):
        assert is_blocked_address(address) is False


class TestTargetParsing:
    def test_non_http_schemes_are_rejected(self):
        with pytest.raises(ValueError):
            parse_target("file:///etc/passwd")
        with pytest.raises(ValueError):
            parse_target("javascript:alert(1)")

    def test_a_url_without_a_host_is_rejected(self):
        with pytest.raises(ValueError):
            parse_target("http:///nowhere")

    def test_host_and_port_are_extracted(self):
        host, port = parse_target("https://Example.com:8443/a/b?c=d")
        assert host == "example.com"
        assert port == 8443

    def test_default_ports_are_filled_in(self):
        assert parse_target("http://example.com/")[1] == 80
        assert parse_target("https://example.com/")[1] == 443


class TestDomainPolicy:
    def test_everything_is_allowed_by_default(self):
        assert DomainPolicy().reason_to_block("example.com") is None

    def test_the_operator_list_blocks(self):
        policy = DomainPolicy(operator_blocked=["ads.example.com"])
        assert policy.reason_to_block("ads.example.com") is not None
        assert policy.reason_to_block("example.com") is None

    def test_the_run_block_list_blocks(self):
        policy = DomainPolicy(blocked=["*.tracker.test"])
        assert policy.reason_to_block("a.tracker.test") is not None

    def test_an_allow_list_blocks_everything_outside_it(self):
        policy = DomainPolicy(allowed=["example.com"])
        assert policy.reason_to_block("shop.example.com") is None
        assert policy.reason_to_block("other.test") is not None

    def test_the_operator_list_wins_over_a_run_allow_list(self):
        policy = DomainPolicy(allowed=["example.com"], operator_blocked=["example.com"])
        assert policy.reason_to_block("example.com") is not None


class TestStructuralRiskDetection:
    def test_a_submit_button_inside_a_form_is_a_submit_control(self):
        assert is_submit_control(tag="button", input_type=None, explicit_type="submit", in_form=True)

    def test_a_form_button_without_a_type_defaults_to_submit(self):
        assert is_submit_control(tag="button", input_type=None, explicit_type=None, in_form=True)

    def test_an_explicit_button_type_is_not_a_submit_control(self):
        assert not is_submit_control(
            tag="button", input_type=None, explicit_type="button", in_form=True
        )

    def test_a_button_outside_a_form_is_not_a_submit_control(self):
        assert not is_submit_control(tag="button", input_type=None, explicit_type=None, in_form=False)

    def test_an_input_of_type_submit_is_a_submit_control(self):
        assert is_submit_control(tag="input", input_type="submit", explicit_type=None, in_form=True)

    def test_a_text_input_is_not_a_submit_control(self):
        assert not is_submit_control(tag="input", input_type="text", explicit_type=None, in_form=True)

    def test_a_link_is_not_a_submit_control(self):
        assert not is_submit_control(tag="a", input_type=None, explicit_type=None, in_form=True)


class TestRiskyUrlPatterns:
    def test_a_glob_pattern_matches_anywhere_in_the_url(self):
        assert matches_risky_url("https://shop.test/checkout/pay", ["*/checkout*"])

    def test_a_non_matching_pattern_is_not_risky(self):
        assert not matches_risky_url("https://shop.test/browse", ["*/checkout*"])

    def test_no_patterns_means_not_risky(self):
        assert not matches_risky_url("https://shop.test/checkout", [])

    def test_matching_ignores_case(self):
        assert matches_risky_url("https://shop.test/CHECKOUT", ["*/checkout*"])


class TestLimitClamping:
    def test_an_absent_request_takes_the_default(self):
        assert clamp_limit(None, default=50, cap=200) == 50

    def test_a_request_within_the_cap_is_honoured(self):
        assert clamp_limit(20, default=50, cap=200) == 20

    def test_a_request_above_the_cap_is_clamped(self):
        assert clamp_limit(5000, default=50, cap=200) == 200

    def test_a_non_positive_request_takes_the_default(self):
        assert clamp_limit(0, default=50, cap=200) == 50
        assert clamp_limit(-3, default=50, cap=200) == 50
