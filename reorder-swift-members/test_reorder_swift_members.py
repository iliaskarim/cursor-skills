#!/usr/bin/env python3
"""Regression tests for reorder_swift_members.py."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import reorder_swift_members as reorder  # noqa: E402

VIEWBUILDER_IF_ELSE = '''\
import SwiftUI

struct SampleView: View {
  var body: some View {
    loadingPlaceholder
  }

  let title: String

  @ViewBuilder private var loadingPlaceholder: some View {
    if usesRedactedLoadingPlaceholder {
      Text("Placeholder markdown body")
    } else {
      Color.clear
    }
  }

  private var resolvedHTMLFragment: String? {
    title
  }

  private var usesRedactedLoadingPlaceholder = true
}
'''


class ComputedVarClassificationTests(unittest.TestCase):
  def test_getter_with_inner_let_is_computed(self) -> None:
    member = """\
  public var urlPath: String {
    let path = url.path
    return path.isEmpty ? "/" : path
  }
"""
    self.assertTrue(reorder.is_computed_instance_var(member))
    self.assertFalse(reorder.is_exposed_stored_property(member))

  def test_stored_let_is_not_computed(self) -> None:
    member = "  public let request: Request\n"
    self.assertFalse(reorder.is_computed_instance_var(member))
    self.assertTrue(reorder.is_exposed_stored_property(member))

  def test_stored_var_with_observers_is_not_computed(self) -> None:
    member = """\
  var count: Int = 0 {
    didSet { print(count) }
  }
"""
    self.assertFalse(reorder.is_computed_instance_var(member))
    self.assertTrue(reorder.is_exposed_stored_property(member))


class FailableInitOrderTests(unittest.TestCase):
  def test_failable_init_is_not_an_exposed_stored_property(self) -> None:
    member = "  init?(url: URL) {\n    self.url = url\n  }\n"
    self.assertEqual(reorder.member_kind(member), "instance_fn")
    self.assertEqual(reorder.member_name(member), "init")
    self.assertTrue(reorder.is_init_member(member))
    self.assertFalse(reorder.is_exposed_stored_property(member))

  def test_required_failable_init_is_init(self) -> None:
    member = "  required init?(coder _: NSCoder) { fatalError() }\n"
    self.assertTrue(reorder.is_init_member(member))
    self.assertEqual(reorder.member_kind(member), "instance_fn")

  def test_internal_func_comes_before_failable_init(self) -> None:
    body = """
  let segments: [String]

  let hostKind: HostKind

  init?(url: URL) {
    segments = []
    hostKind = .github
    queryItems = []
  }

  private let queryItems: [URLQueryItem]

  func queryValue(named name: String) -> String? {
    queryItems.first { $0.name == name }?.value
  }
"""
    new_body = reorder.reorder_plain_type_body(body, is_enum=False)
    query_items_at = new_body.index("private let queryItems")
    query_value_at = new_body.index("func queryValue")
    init_at = new_body.index("init?(url: URL)")
    self.assertLess(query_items_at, query_value_at)
    self.assertLess(query_value_at, init_at)


class ExtensionMemberOrderTests(unittest.TestCase):
  def test_finds_top_level_extension_span(self) -> None:
    text = """\
import SwiftUI

extension Color {
  init?(gitHubHex: String?) { self.init(red: 0, green: 0, blue: 0) }

  static func gitHubLabelForeground(hex: String?) -> Color { .black }
}
"""
    spans = reorder.find_type_spans(text, include_views=True, include_non_views=True)
    self.assertEqual(len(spans), 1)
    _start, _end, name, kind, is_view = spans[0]
    self.assertEqual(name, "Color")
    self.assertEqual(kind, "extension")
    self.assertFalse(is_view)

  def test_static_helpers_come_before_failable_init(self) -> None:
    body = """
  init?(gitHubHex: String?) {
    self.init(red: 0, green: 0, blue: 0)
  }

  static func gitHubLabelForeground(hex: String?) -> Color {
    .black
  }

  static func gitHubHexRelativeLuminance(_ hex: String?) -> Double? {
    nil
  }

  private static func gitHubHexRGB(_ hex: String?) -> (Double, Double, Double)? {
    nil
  }
"""
    new_body = reorder.reorder_plain_type_body(body, is_enum=False)
    foreground_at = new_body.index("static func gitHubLabelForeground")
    luminance_at = new_body.index("static func gitHubHexRelativeLuminance")
    rgb_at = new_body.index("private static func gitHubHexRGB")
    init_at = new_body.index("init?(gitHubHex:")
    self.assertLess(luminance_at, foreground_at)
    self.assertLess(foreground_at, rgb_at)
    self.assertLess(rgb_at, init_at)


class EndpointComputedVarOrderTests(unittest.TestCase):
  def test_http_header_fields_sorts_before_url_path(self) -> None:
    body = """
  public typealias Response = Data

  public var urlPath: String {
    let path = url.path
    return path.isEmpty ? "/" : path
  }

  public var httpHeaderFields: [String: String] {
    ["Accept": "*/*"]
  }

  public var urlHost: String {
    url.host ?? ""
  }

  private let url: URL

  public init(url: URL) {
    self.url = url
  }
"""
    new_body = reorder.reorder_plain_type_body(body, is_enum=False)
    header_at = new_body.index("public var httpHeaderFields")
    host_at = new_body.index("public var urlHost")
    path_at = new_body.index("public var urlPath")
    self.assertLess(header_at, host_at)
    self.assertLess(host_at, path_at)
    self.assertTrue(reorder.is_computed_instance_var(
        "  public var urlPath: String {\n    let path = url.path\n  }\n"
    ))

  def test_computed_inserts_by_name_among_exposed_stored(self) -> None:
    """``contains…`` must not land after every stored ``let`` (``download…``)."""
    body = """
  public let blobURL: URL?

  public let content: Data?

  public let size: Int?

  public let submoduleGitURL: String?

  public let downloadURL: URL?

  public var containsGitLFSPointer: Bool {
    content != nil
  }
"""
    new_body = reorder.reorder_plain_type_body(body, is_enum=False)
    blob_at = new_body.index("public let blobURL")
    contains_at = new_body.index("public var containsGitLFSPointer")
    content_at = new_body.index("public let content")
    size_at = new_body.index("public let size")
    submodule_at = new_body.index("public let submoduleGitURL")
    download_at = new_body.index("public let downloadURL")
    self.assertLess(blob_at, contains_at)
    self.assertLess(contains_at, content_at)
    self.assertLess(content_at, size_at)
    self.assertLess(size_at, submodule_at)
    self.assertLess(submodule_at, download_at)
    self.assertLess(contains_at, download_at)

  def test_internal_path_component_follows_public_vars(self) -> None:
    """Default-internal ``pathComponent`` must not interleave public lets."""
    body = """
  public let httpMethod = "PATCH"

  var pathComponent: String {
    "/pulls"
  }

  public let request: Request

  public var httpHeaderFields: [String: String] {
    [:]
  }

  private let owner: String
"""
    new_body = reorder.reorder_plain_type_body(body, is_enum=False)
    header_at = new_body.index("public var httpHeaderFields")
    method_at = new_body.index("public let httpMethod")
    request_at = new_body.index("public let request")
    path_at = new_body.index("var pathComponent")
    owner_at = new_body.index("private let owner")
    self.assertLess(header_at, method_at)
    self.assertLess(method_at, request_at)
    self.assertLess(request_at, path_at)
    self.assertLess(path_at, owner_at)


class SelfFactoryConventionTests(unittest.TestCase):
  def test_rewrites_return_type_and_constructor(self) -> None:
    body = """
  static func parse(owner: String, name: String) -> RepoRoute {
    return RepoRoute(owner: owner, name: name)
  }
"""
    new_body = reorder.apply_self_factory_conventions(body, "RepoRoute")
    self.assertIn("-> Self {", new_body)
    self.assertIn("return .init(owner: owner, name: name)", new_body)
    self.assertNotIn("RepoRoute", new_body)

  def test_rewrites_optional_return_type(self) -> None:
    body = """
  static func parse(path: String) -> FileRoute? {
    FileRoute(path: path)
  }
"""
    new_body = reorder.apply_self_factory_conventions(body, "FileRoute")
    self.assertIn("-> Self? {", new_body)
    self.assertIn(".init(path: path)", new_body)

  def test_leaves_other_return_types_alone(self) -> None:
    body = """
  static func appRoute() -> AppRoute {
    AppRoute(RepoRoute.parse(owner: "o", name: "n", branch: nil))
  }
"""
    new_body = reorder.apply_self_factory_conventions(body, "RepoRoute")
    self.assertIn("-> AppRoute {", new_body)
    self.assertIn("AppRoute(RepoRoute.parse(", new_body)

  def test_leaves_zero_argument_constructors_alone(self) -> None:
    body = """
  var secondsAgo: Int {
    Int(Date().timeIntervalSince(self))
  }

  static let spellOutFormatter: NumberFormatter = {
    let formatter = NumberFormatter()
    formatter.numberStyle = .spellOut
    return formatter
  }()
"""
    date_body = reorder.apply_self_factory_conventions(body, "Date")
    self.assertIn("Int(Date().timeIntervalSince(self))", date_body)
    self.assertNotIn("Int(.init()", date_body)

    formatter_body = reorder.apply_self_factory_conventions(body, "NumberFormatter")
    self.assertIn("let formatter = NumberFormatter()", formatter_body)
    self.assertNotIn("let formatter = .init()", formatter_body)

  def test_skips_class_types(self) -> None:
    body = """
  static func restoreFromKeychain() -> UserSession {
    let session = UserSession()
    return session
  }

  static let debug = UserSession(accessToken: "token")
"""
    new_body = reorder.apply_self_factory_conventions(body, "UserSession", "class")
    self.assertEqual(new_body, body)

  def test_skips_known_class_type_extensions(self) -> None:
    body = """
  static func monospacedBodyFont() -> UIFont {
    UIFontMetrics(forTextStyle: .body).scaledFont(
      for: .monospacedSystemFont(ofSize: 12, weight: .regular)
    )
  }
"""
    new_body = reorder.apply_self_factory_conventions(body, "UIFont", "extension")
    self.assertEqual(new_body, body)
    self.assertTrue(reorder.is_class_type_name("UIFont", "extension"))
    self.assertFalse(reorder.is_class_type_name("Color", "extension"))

  def test_leaves_untyped_constructor_assignments_alone(self) -> None:
    body = """
  static func make(token: String) -> RepoRoute {
    RepoRoute(token: token)
  }

  static let preview = RepoRoute(token: "t")
"""
    new_body = reorder.apply_self_factory_conventions(body, "RepoRoute", "struct")
    self.assertIn("-> Self {", new_body)
    self.assertIn(".init(token: token)", new_body)
    self.assertIn('static let preview = RepoRoute(token: "t")', new_body)
    self.assertNotIn("static let preview = .init(", new_body)

  def test_leaves_notification_name_static_lets_alone(self) -> None:
    body = """
  static let didDismiss = Notification.Name("DidDismiss")

  static let didPresent = Notification.Name("DidPresent")
"""
    new_body = reorder.apply_self_factory_conventions(
        body, "Notification.Name", "extension"
    )
    self.assertIn('static let didDismiss = Notification.Name("DidDismiss")', new_body)
    self.assertIn('static let didPresent = Notification.Name("DidPresent")', new_body)
    self.assertNotIn("= .init(", new_body)


class DotInitInferenceTests(unittest.TestCase):
  def test_rewrites_existing_typed_assignment(self) -> None:
    text = "  let route: AppRoute = AppRoute(RepoRoute())\n"
    new = reorder.apply_dot_init_inference(text)
    self.assertEqual(new, "  let route: AppRoute = .init(RepoRoute())\n")

  def test_does_not_add_type_annotation_to_use_dot_init(self) -> None:
    text = "  let route = AppRoute(RepoRoute())\n"
    new = reorder.apply_dot_init_inference(text)
    self.assertEqual(new, text)
    self.assertNotIn(": AppRoute = .init", new)


class AttributeLineTests(unittest.TestCase):
  def test_viewbuilder_same_line_is_not_attribute_only(self) -> None:
    line = "@ViewBuilder private var loadingPlaceholder: some View {"
    self.assertFalse(reorder.is_attribute_only_line(line))

  def test_lone_viewbuilder_is_attribute_only(self) -> None:
    self.assertTrue(reorder.is_attribute_only_line("@ViewBuilder"))

  def test_environment_wrapper_is_attribute_only(self) -> None:
    self.assertTrue(reorder.is_attribute_only_line("@Environment(\\.colorScheme)"))


class AclOfTests(unittest.TestCase):
  def test_long_doc_comment_does_not_hide_private(self) -> None:
    member = """\
  /// One
  ///
  /// Two
  /// Three
  /// Four
  /// Five
  @MainActor
  private func refreshRepoDetailScreen() async {}
"""
    self.assertEqual(reorder.acl_of(member), "private")
    self.assertTrue(reorder.is_private_member(member))

  def test_doc_mentioning_private_does_not_flip_internal_init(self) -> None:
    member = """\
  /// Keep private helpers after init.
  init(owner: String) {
    self.owner = owner
  }
"""
    self.assertEqual(reorder.acl_of(member), "internal")
    self.assertFalse(reorder.is_private_member(member))
    self.assertTrue(reorder.is_init_member(member))

  def test_private_set_ranks_by_read_access(self) -> None:
    self.assertEqual(reorder.acl_of("  private(set) var count = 0\n"), "internal")
    self.assertEqual(
        reorder.acl_of("  public private(set) var count = 0\n"),
        "public",
    )
    self.assertFalse(reorder.is_private_member("  private(set) var count = 0\n"))

  def test_long_doc_private_func_sorts_after_init(self) -> None:
    body = """
  var body: some View {
    EmptyView()
  }

  /// One
  ///
  /// Two
  /// Three
  /// Four
  /// Five
  @MainActor
  private func refreshRepoDetailScreen() async {}

  init(owner: String) {
    self.owner = owner
  }
"""
    new_body = reorder.reorder_swiftui_body(body)
    self.assertLess(new_body.index("init("), new_body.index("private func refreshRepoDetailScreen"))


class ViewBuilderSplitTests(unittest.TestCase):
  def test_if_else_viewbuilder_stays_one_member(self) -> None:
    members = reorder.split_top_level_members(
        """
  @ViewBuilder private var loadingPlaceholder: some View {
    if usesRedactedLoadingPlaceholder {
      Text("a")
    } else {
      Color.clear
    }
  }

  private var resolvedHTMLFragment: String? {
    title
  }
"""
    )
    joined = "\n---\n".join(members)
    self.assertEqual(len(members), 2, joined)
    self.assertIn("loadingPlaceholder", members[0])
    self.assertIn("else {", members[0])
    self.assertTrue(members[0].rstrip().endswith("}"))
    self.assertIn("resolvedHTMLFragment", members[1])

  def test_reorder_does_not_close_type_before_private_vars(self) -> None:
    body = """
  var body: some View {
    loadingPlaceholder
  }

  let title: String

  @ViewBuilder private var loadingPlaceholder: some View {
    if flag {
      Text("a")
    } else {
      Text("b")
    }
  }

  private var resolvedHTMLFragment: String? {
    title
  }

  private var flag = true
"""
    new_body = reorder.reorder_swiftui_body(body)
    struct = "struct SampleView: View {" + new_body + "}\n"
    start = struct.index("{")
    depth = 0
    struct_end = None
    for i, ch in enumerate(struct[start:], start):
      if ch == "{":
        depth += 1
      elif ch == "}":
        depth -= 1
        if depth == 0:
          struct_end = i
          break
    fragment_at = struct.index("private var resolvedHTMLFragment")
    self.assertIsNotNone(struct_end)
    self.assertLess(fragment_at, struct_end)
    self.assertEqual(new_body.count("{"), body.count("{"))
    self.assertEqual(new_body.count("}"), body.count("}"))


class ProcessFileTests(unittest.TestCase):
  def test_process_file_keeps_viewbuilder_members_in_type(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      path = Path(tmp) / "SampleView.swift"
      path.write_text(VIEWBUILDER_IF_ELSE)
      reorder.process_file(path, include_views=True, include_non_views=False)
      text = path.read_text()
      fragment_at = text.index("private var resolvedHTMLFragment")
      start = text.index("{", text.index("struct SampleView"))
      depth = 0
      struct_end = None
      for i, ch in enumerate(text[start:], start):
        if ch == "{":
          depth += 1
        elif ch == "}":
          depth -= 1
          if depth == 0:
            struct_end = i
            break
      self.assertIsNotNone(struct_end)
      self.assertLess(fragment_at, struct_end)
      self.assertIn("@ViewBuilder private var loadingPlaceholder", text)
      self.assertEqual(text.count("{"), VIEWBUILDER_IF_ELSE.count("{"))
      self.assertEqual(text.count("}"), VIEWBUILDER_IF_ELSE.count("}"))


class EnumCaseLayoutTests(unittest.TestCase):
  def test_simple_cases_share_one_line(self) -> None:
    body = """
      case video
      case audio
"""
    self.assertEqual(
        reorder.format_enum_body_cases(body).strip(),
        "case video, audio",
    )

  def test_raw_value_cases_stay_separate(self) -> None:
    body = """
    case callbackURLScheme = "CALLBACK_URL_SCHEME", oauthClientID = "OAUTH_CLIENT_ID",
         oauthClientSecret = "OAUTH_CLIENT_SECRET"
"""
    formatted = reorder.format_enum_body_cases(body)
    self.assertIn('case callbackURLScheme = "CALLBACK_URL_SCHEME"', formatted)
    self.assertIn('case oauthClientID = "OAUTH_CLIENT_ID"', formatted)
    self.assertIn('case oauthClientSecret = "OAUTH_CLIENT_SECRET"', formatted)
    self.assertNotIn(", oauthClientID", formatted)
    self.assertNotIn(", oauthClientSecret", formatted)

  def test_already_comma_separated_is_stable(self) -> None:
    body = "\n    case repos, users\n"
    self.assertEqual(reorder.format_enum_body_cases(body), body)

  def test_associated_cases_keep_blank_lines(self) -> None:
    body = """
  case issue(Issue)
  case pullRequest(PullRequest)
"""
    formatted = reorder.format_enum_body_cases(body)
    self.assertIn("case issue(Issue)\n\n  case pullRequest(PullRequest)", formatted)

  def test_mix_simple_then_associated(self) -> None:
    body = """
  case push
  case fork
  case issue(Issue)
"""
    formatted = reorder.format_enum_body_cases(body)
    self.assertIn("case push, fork\n\n  case issue(Issue)", formatted)

  def test_commented_case_stays_separate(self) -> None:
    body = """
  case push
  /// Docs keep this declaration separate.
  case release
  case fork
"""
    formatted = reorder.format_enum_body_cases(body)
    self.assertIn("case push", formatted)
    self.assertIn("/// Docs keep this declaration separate.", formatted)
    self.assertIn("case release", formatted)
    self.assertIn("case fork", formatted)
    self.assertNotIn("case push, release", formatted)
    self.assertNotIn("case release, fork", formatted)

  def test_wraps_at_one_hundred_ten_columns(self) -> None:
    names = [f"case{i:02d}Name" for i in range(12)]
    body = "\n  " + "\n  ".join(f"case {name}" for name in names) + "\n"
    formatted = reorder.format_enum_body_cases(body)
    lines = [line for line in formatted.splitlines() if line.strip()]
    self.assertGreater(len(lines), 1)
    for line in lines[:-1]:
      self.assertTrue(line.rstrip().endswith(","), line)
      self.assertLessEqual(len(line), reorder.ENUM_CASE_LINE_LENGTH)
    self.assertFalse(lines[-1].rstrip().endswith(","))
    self.assertLessEqual(len(lines[-1]), reorder.ENUM_CASE_LINE_LENGTH)
    joined = " ".join(line.strip().rstrip(",") for line in lines)
    for name in names:
      self.assertIn(name, joined)

  def test_wrap_is_valid_swift_and_idempotent(self) -> None:
    names = [f"case{i:02d}Name" for i in range(12)]
    body = "\n  " + "\n  ".join(f"case {name}" for name in names) + "\n"
    once = reorder.format_enum_body_cases(body)
    twice = reorder.format_enum_body_cases(once)
    self.assertEqual(once, twice)
    self.assertIn("case ", once)
    for line in once.splitlines():
      if line.strip() and not line.strip().startswith("case "):
        self.assertTrue(line.strip()[0].isalpha() or line.strip()[0] == "`")
    # Continuation lines follow a comma on the previous line.
    code_lines = [line for line in once.splitlines() if line.strip()]
    for prev, nxt in zip(code_lines, code_lines[1:]):
      if not nxt.strip().startswith("case "):
        self.assertTrue(prev.rstrip().endswith(","), prev)

  def test_nested_enum_in_file(self) -> None:
    text = """\
struct Host {
  enum TrackType: UInt8 {
    case video = 1
    case audio = 2
  }
}
"""
    formatted = reorder.format_enum_cases_in_text(text)
    self.assertIn("case video = 1\n\n    case audio = 2", formatted)


class MergeSafetyTests(unittest.TestCase):
  def test_detects_conflict_markers(self) -> None:
    text = (
      "class Sample {\n"
      "<<<<<<< HEAD\n"
      "  func beta() {}\n"
      "=======\n"
      "  func alpha() {}\n"
      ">>>>>>> branch\n"
      "}\n"
    )
    self.assertTrue(reorder.has_unresolved_conflict_markers(text))
    self.assertFalse(reorder.has_unresolved_conflict_markers("class Sample {}\n"))

  def test_process_file_skips_conflict_markers(self) -> None:
    original = (
      "class Sample {\n"
      "<<<<<<< HEAD\n"
      "  func beta() {}\n"
      "=======\n"
      "  func alpha() {}\n"
      ">>>>>>> branch\n"
      "\n"
      "  let name: String\n"
      "}\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
      path = Path(tmp) / "Sample.swift"
      path.write_text(original)
      changed = reorder.process_file(
        path, include_views=True, include_non_views=True
      )
      self.assertFalse(changed)
      self.assertEqual(path.read_text(), original)

  def test_process_file_skips_unbalanced_braces(self) -> None:
    original = "class Sample {\n  func beta() {}\n  let name: String\n"
    with tempfile.TemporaryDirectory() as tmp:
      path = Path(tmp) / "Sample.swift"
      path.write_text(original)
      changed = reorder.process_file(
        path, include_views=True, include_non_views=True
      )
      self.assertFalse(changed)
      self.assertEqual(path.read_text(), original)

  def test_file_scope_instance_member_count(self) -> None:
    nested = "class Pager {\n  func load() {}\n}\n"
    hoisted = "class Pager {\n}\n\nfunc load() {}\n"
    self.assertEqual(reorder.file_scope_instance_member_count(nested), 0)
    self.assertEqual(reorder.file_scope_instance_member_count(hoisted), 1)
    self.assertTrue(reorder.rewrite_is_unsafe(nested, hoisted))

  def test_process_file_still_reorders_a_compiling_type(self) -> None:
    original = "class Sample {\n  func beta() {}\n\n  let name: String\n}\n"
    with tempfile.TemporaryDirectory() as tmp:
      path = Path(tmp) / "Sample.swift"
      path.write_text(original)
      changed = reorder.process_file(
        path, include_views=True, include_non_views=True
      )
      self.assertTrue(changed)
      text = path.read_text()
      self.assertLess(text.index("let name"), text.index("func beta"))
      self.assertEqual(text.count("{"), original.count("{"))
      self.assertEqual(text.count("}"), original.count("}"))


class DebugIfBlockTests(unittest.TestCase):
  def test_split_unwraps_debug_members_individually(self) -> None:
    body = """\
  static func zebra() {}

#if DEBUG
  static let debug = 1

  private static var debugCount = 0

  private static func debugHelper() {}
#endif

  private static func alpha() {}
"""
    members = reorder.split_top_level_members(body)
    self.assertEqual(len(members), 5)
    self.assertTrue(members[1].strip().startswith("#if DEBUG"))
    self.assertIn("static let debug", members[1])
    self.assertTrue(members[1].strip().endswith("#endif"))
    self.assertTrue(members[2].strip().startswith("#if DEBUG"))
    self.assertIn("private static var debugCount", members[2])
    self.assertTrue(members[3].strip().startswith("#if DEBUG"))
    self.assertIn("private static func debugHelper", members[3])
    self.assertFalse(members[0].strip().startswith("#if"))
    self.assertFalse(members[4].strip().startswith("#if"))

  def test_reorder_sorts_as_if_debug_absent_then_rethrows(self) -> None:
    body = """\
  static func zebra() {}

#if DEBUG
  static let debug = 1

  private static var debugCount = 0

  private static func debugHelper() {}
#endif

  private static func alpha() {}
"""
    reordered = reorder.reorder_plain_type_body(body, is_enum=False)
    debug_let = reordered.index("static let debug")
    zebra = reordered.index("static func zebra")
    debug_count = reordered.index("private static var debugCount")
    alpha = reordered.index("private static func alpha")
    debug_helper = reordered.index("private static func debugHelper")
    # Kind-first: all static vars before static funcs; ACL then name within kind.
    self.assertLess(debug_let, debug_count)
    self.assertLess(debug_count, zebra)
    self.assertLess(zebra, alpha)
    self.assertLess(alpha, debug_helper)
    # each DEBUG member wrapped on its own
    self.assertEqual(reordered.count("#if DEBUG"), 3)
    self.assertEqual(reordered.count("#endif"), 3)

  def test_debug_else_block_stays_opaque(self) -> None:
    body = """\
#if DEBUG
  static let debug = 1
#else
  static let debug = 0
#endif

  static func alpha() {}
"""
    members = reorder.split_top_level_members(body)
    self.assertEqual(len(members), 2)
    self.assertIn("#else", members[0])
    self.assertIn("static let debug = 1", members[0])
    self.assertIn("static let debug = 0", members[0])

  def test_split_is_idempotent_for_already_wrapped_members(self) -> None:
    body = """\
#if DEBUG
  static let debug = 1
#endif

#if DEBUG
  private static func debugHelper() {}
#endif
"""
    once = reorder.split_top_level_members(body)
    twice = reorder.split_top_level_members(reorder.join_members(once))
    self.assertEqual(len(once), 2)
    self.assertEqual(len(twice), 2)
    self.assertEqual(once[0].count("#if DEBUG"), 1)
    self.assertEqual(twice[0].count("#if DEBUG"), 1)

  def test_adjacent_debug_previews_share_one_wrapper(self) -> None:
    text = """\
import SwiftUI

struct ChangedFileLabel: View {
  var body: some View { Text("hi") }
}

#if DEBUG
private func previewFixture() -> Int { 1 }

#Preview("Default") {
  Text("a")
}

#Preview("Large") {
  Text("b")
}
#endif

#Preview("Placeholder") {
  Text("c")
}
"""
    path = Path("ChangedFileLabel.swift")
    reordered = reorder.reorder_file_layout(text, path)
    helper_at = reordered.index("private func previewFixture")
    view_at = reordered.index("struct ChangedFileLabel")
    default_at = reordered.index('#Preview("Default")')
    large_at = reordered.index('#Preview("Large")')
    placeholder_at = reordered.index('#Preview("Placeholder")')
    self.assertLess(helper_at, view_at)
    self.assertLess(view_at, default_at)
    self.assertLess(default_at, large_at)
    self.assertLess(large_at, placeholder_at)
    # Helper stays wrapped on its own; the two DEBUG previews share one block.
    self.assertEqual(reordered.count("#if DEBUG"), 2)
    self.assertEqual(reordered.find("#endif", default_at, large_at), -1)
    self.assertLess(reordered.rfind("#endif", 0, placeholder_at), placeholder_at)
    self.assertEqual(reordered.find("#if DEBUG", large_at, placeholder_at), -1)
    twice = reorder.reorder_file_layout(reordered, path)
    self.assertEqual(reordered, twice)

  def test_debug_endif_inside_preview_string_stays_in_the_block(self) -> None:
    text = """\
import SwiftUI

#if DEBUG
#Preview("Diff") {
  let sample = \"\"\"
  #endif
  \"\"\"
  Text(sample)
}

#Preview("Other") {
  Text("b")
}
#endif
"""
    reordered = reorder.reorder_file_layout(text, Path("PatchView.swift"))
    diff_at = reordered.index('#Preview("Diff")')
    other_at = reordered.index('#Preview("Other")')
    between = reordered[diff_at:other_at]
    self.assertLess(diff_at, other_at)
    # The sample's ``#endif`` stays inside the string; no real directive
    # closes the DEBUG block between the two previews.
    self.assertFalse(
        any(line.startswith("#endif") for line in between.splitlines())
    )
    self.assertEqual(reordered.count("#if DEBUG"), 1)
    self.assertIn("#endif\n  \"\"\"", reordered)

  def test_adjacent_debug_preview_helpers_share_one_wrapper(self) -> None:
    text = """\
import SwiftUI

private extension CommitViewData {
  var title: String { "" }
}

#if DEBUG
private let squashJSON = \"\"\"
{ "sha": "abc" }
\"\"\"

private func previewCommit(from json: String) -> Int { json.count }

private let otherJSON = \"\"\"
{ "sha": "def" }
\"\"\"
#endif

struct CommitRow: View {
  var body: some View { Text("hi") }
}
"""
    path = Path("CommitRow.swift")
    reordered = reorder.reorder_file_layout(text, path)
    self.assertLess(reordered.index("extension CommitViewData"), reordered.index("#if DEBUG"))
    func_at = reordered.index("func previewCommit")
    other_at = reordered.index("let otherJSON")
    squash_at = reordered.index("let squashJSON")
    self.assertLess(func_at, other_at)
    self.assertLess(other_at, squash_at)
    self.assertLess(reordered.index("#endif"), reordered.index("struct CommitRow"))
    self.assertEqual(reordered.count("#if DEBUG"), 1)
    between = reordered[func_at:squash_at]
    self.assertFalse(
        any(
            line.startswith("#if") or line.startswith("#endif")
            for line in between.splitlines()
        )
    )
    twice = reorder.reorder_file_layout(reordered, path)
    self.assertEqual(reordered, twice)

  def test_non_debug_helper_keeps_debug_neighbors_split(self) -> None:
    text = """\
import SwiftUI

#if DEBUG
private func previewCommit() -> Int { 1 }
#endif

private func productionHelper() -> Int { 0 }

#if DEBUG
private let squashJSON = "abc"
#endif

struct CommitRow: View {
  var body: some View { Text("hi") }
}
"""
    reordered = reorder.reorder_file_layout(text, Path("CommitRow.swift"))
    preview_at = reordered.index("func previewCommit")
    production_at = reordered.index("func productionHelper")
    json_at = reordered.index("let squashJSON")
    self.assertLess(preview_at, production_at)
    self.assertLess(production_at, json_at)
    self.assertEqual(reordered.count("#if DEBUG"), 2)
    self.assertLess(reordered.find("#endif"), production_at)
    self.assertGreater(reordered.rfind("#if DEBUG"), production_at)

  def test_debug_imports_stay_inside_coalesced_debug_types(self) -> None:
    """``DebugKingfisherConfiguration``: one DEBUG wrapper, imports on top.

    Adjacent DEBUG chunks coalesce; imports move to the top of that shared
    wrapper — not a second preamble ``#if DEBUG``. Second pass is a no-op.
    """
    text = """\
#if DEBUG
private struct DebugImageDownloadDelayModifier {
  func modified() {}
}
#endif

#if DEBUG
import Foundation
import Kingfisher

enum DebugKingfisherConfiguration {
  static func ensureConfigured() {}
}
#endif
"""
    path = Path("DebugKingfisherConfiguration.swift")
    once = reorder.reorder_file_layout(text, path)
    twice = reorder.reorder_file_layout(once, path)
    self.assertEqual(once, twice)
    self.assertEqual(once.count("#if DEBUG"), 1)
    self.assertEqual(once.count("#endif"), 1)
    import_at = once.index("import Foundation")
    kingfisher_at = once.index("import Kingfisher")
    struct_at = once.index("struct DebugImageDownloadDelayModifier")
    enum_at = once.index("enum DebugKingfisherConfiguration")
    self.assertLess(once.index("#if DEBUG"), import_at)
    self.assertLess(import_at, kingfisher_at)
    self.assertLess(kingfisher_at, struct_at)
    self.assertLess(struct_at, enum_at)
    self.assertLess(enum_at, once.index("#endif"))
    between = once[struct_at:enum_at]
    self.assertNotIn("#endif", between)
    self.assertNotIn("#if DEBUG", between)

  def test_mixed_debug_import_and_type_stay_one_wrapper(self) -> None:
    """``UIColor+CSSHex``: do not split import + extension into two DEBUGs."""
    text = """\
#if DEBUG
import UIKit

extension UIColor {
  var cssHex: String? { nil }
}
#endif
"""
    path = Path("UIColor+CSSHex.swift")
    once = reorder.reorder_file_layout(text, path)
    twice = reorder.reorder_file_layout(once, path)
    self.assertEqual(once, twice)
    self.assertEqual(once.count("#if DEBUG"), 1)
    self.assertEqual(once.count("#endif"), 1)
    self.assertLess(once.index("import UIKit"), once.index("extension UIColor"))
    self.assertLess(once.index("extension UIColor"), once.index("#endif"))


class ImportSplitTests(unittest.TestCase):
  def test_testable_import_stays_with_import_block(self) -> None:
    text = """\
import Foundation
import Testing
@testable import Octodoge

private func fixture() -> Int { 1 }
"""
    imports, rest = reorder.split_imports(text)
    self.assertIn("@testable import Octodoge", imports)
    self.assertTrue(rest.lstrip().startswith("private func"))

  def test_debug_import_only_block_stays_with_imports(self) -> None:
    text = """\
import SwiftUI

#if DEBUG
import Foundation
#endif

struct RootView {
  var body: Int { 1 }
}
"""
    imports, rest = reorder.split_imports(text)
    self.assertIn("import SwiftUI", imports)
    self.assertIn("import Foundation", imports)
    self.assertIn("#if DEBUG", imports)
    self.assertTrue(rest.lstrip().startswith("struct RootView"))


class DebugIfImportLayoutTests(unittest.TestCase):
  def test_whole_file_debug_import_stays_above_type(self) -> None:
    text = """\
#if DEBUG
import Foundation

enum DebugNetworkDelay {
  static let unlimitedFailCount = -1
}
#endif
"""
    reordered = reorder.reorder_file_layout(text, Path("DebugNetworkDelay.swift"))
    import_at = reordered.index("import Foundation")
    enum_at = reordered.index("enum DebugNetworkDelay")
    first_endif = reordered.index("#endif")
    self.assertEqual(reordered.count("#if DEBUG"), 1)
    self.assertLess(reordered.index("#if DEBUG"), import_at)
    self.assertLess(import_at, enum_at)
    self.assertLess(enum_at, first_endif)
    self.assertLess(reordered.rfind("import Foundation"), reordered.rfind("enum DebugNetworkDelay"))

  def test_debug_import_among_normal_imports_stays_in_preamble(self) -> None:
    text = """\
import SwiftUI

#if DEBUG
import Foundation
#endif

struct RootView {
  var body: Int { 1 }
}
"""
    reordered = reorder.reorder_file_layout(text, Path("RootView.swift"))
    self.assertLess(reordered.index("import SwiftUI"), reordered.index("import Foundation"))
    self.assertLess(reordered.index("import Foundation"), reordered.index("struct RootView"))

  def test_whole_file_debug_import_layout_is_idempotent(self) -> None:
    text = """\
#if DEBUG
import Combine
import Darwin
import Foundation

final class DebugPrintCapture {
  static let shared = DebugPrintCapture()
}
#endif
"""
    path = Path("DebugPrintCapture.swift")
    once = reorder.reorder_file_layout(text, path)
    twice = reorder.reorder_file_layout(once, path)
    self.assertEqual(once, twice)
    self.assertLess(once.index("import Combine"), once.index("final class DebugPrintCapture"))

  def test_process_file_does_not_move_debug_import_to_end(self) -> None:
    original = """\
#if DEBUG
import Foundation

enum DebugNetworkDelay {
  static let unlimitedFailCount = -1
}
#endif
"""
    with tempfile.TemporaryDirectory() as tmp:
      path = Path(tmp) / "DebugNetworkDelay.swift"
      path.write_text(original)
      reorder.process_file(path, include_views=True, include_non_views=True)
      text = path.read_text()
      self.assertLess(text.index("import Foundation"), text.index("enum DebugNetworkDelay"))
      self.assertFalse(text.rstrip().endswith("import Foundation\n#endif"))


class PrivateExtensionOfNestedTypeTests(unittest.TestCase):
  def test_array_of_nested_type_stays_after_main_type(self) -> None:
    """``private extension [Board.Vector]`` is an extension of a nested type.

    The ``[`` sugar is still an extension of ``Array``, but the element type
    is a subtype of the file's primary type, so the chunk stays after
    ``Board`` in source order. Unrelated private extensions still sort into
    the preamble. A second pass is a no-op.
    """
    text = """\
private extension Square {
  func step() -> Int { 0 }
}

private extension Piece {
  func paths() -> [Board.Vector] { [] }
}

public struct Board {
  struct Vector {
    let files: Int

    let ranks: Int
  }

  enum Status {
    case check
  }
}

extension Board.Status: CustomStringConvertible {
  public var description: String { "" }
}

private extension Board.Vector {
  var length: Int { files + ranks }
}

private extension [Board.Vector] {
  static let cardinalUnitVectors: Self = []
}
"""
    path = Path("Board.swift")
    reordered = reorder.reorder_file_layout(text, path)
    piece_at = reordered.index("private extension Piece")
    square_at = reordered.index("private extension Square")
    board_at = reordered.index("public struct Board")
    status_at = reordered.index("extension Board.Status")
    vector_at = reordered.index("private extension Board.Vector")
    array_at = reordered.index("private extension [Board.Vector]")
    self.assertLess(piece_at, square_at)
    self.assertLess(square_at, board_at)
    self.assertLess(board_at, status_at)
    self.assertLess(status_at, vector_at)
    self.assertLess(vector_at, array_at)
    twice = reorder.reorder_file_layout(reordered, path)
    self.assertEqual(reordered, twice)

  def test_fileprivate_array_extension_stays_after_main_type(self) -> None:
    text = """\
fileprivate extension Piece {
  func paths() -> Int { 0 }
}

struct Board {
  struct Vector {
    let files: Int
  }
}

fileprivate extension [Board.Vector] {
  static let diagonalUnitVectors: Self = []
}
"""
    reordered = reorder.reorder_file_layout(text, Path("Board.swift"))
    self.assertLess(
        reordered.index("fileprivate extension Piece"),
        reordered.index("struct Board"),
    )
    self.assertLess(
        reordered.index("struct Board"),
        reordered.index("fileprivate extension [Board.Vector]"),
    )

  def test_array_extension_follows_primary_type_extensions(self) -> None:
    """Sugar stays below ``extension Board: Collection`` even when it led the file."""
    text = """\
private extension [Board.Vector] {
  static let cardinalUnitVectors: Self = []
}

public struct Board {
  struct Vector {
    let files: Int

    let ranks: Int
  }
}

extension Board: Collection {
  public var startIndex: Int { 0 }
}

extension Board.Status: CustomStringConvertible {
  public var description: String { "" }
}
"""
    path = Path("Board.swift")
    reordered = reorder.reorder_file_layout(text, path)
    board_at = reordered.index("public struct Board")
    collection_at = reordered.index("extension Board: Collection")
    status_at = reordered.index("extension Board.Status")
    array_at = reordered.index("private extension [Board.Vector]")
    self.assertLess(board_at, collection_at)
    self.assertLess(collection_at, status_at)
    self.assertLess(status_at, array_at)
    twice = reorder.reorder_file_layout(reordered, path)
    self.assertEqual(reordered, twice)

  def test_optional_extension_follows_plain_type(self) -> None:
    """``Square?`` sorts after ``Square`` even when it led the file."""
    text = """\
private extension Move {
  func transforms() -> Int { 0 }
}

private extension Square? {
  static func + (lhs: Self, rhs: Int) -> Self { lhs }
}

private extension Move.Translation {
  func matches() -> Bool { false }
}

private extension Piece {
  func paths() -> Int { 0 }
}

private extension Square {
  func step() -> Int { 0 }
}

public struct Board {
  struct Vector {
    let files: Int
  }
}
"""
    path = Path("Board.swift")
    reordered = reorder.reorder_file_layout(text, path)
    move_at = reordered.index("private extension Move {")
    translation_at = reordered.index("private extension Move.Translation")
    piece_at = reordered.index("private extension Piece")
    square_at = reordered.index("private extension Square {")
    optional_at = reordered.index("private extension Square?")
    board_at = reordered.index("public struct Board")
    self.assertLess(move_at, translation_at)
    self.assertLess(translation_at, piece_at)
    self.assertLess(piece_at, square_at)
    self.assertLess(square_at, optional_at)
    self.assertLess(optional_at, board_at)
    twice = reorder.reorder_file_layout(reordered, path)
    self.assertEqual(reordered, twice)


class ProtocolBodyReorderTests(unittest.TestCase):
  def test_wrapped_return_type_stays_with_func_body(self) -> None:
    body = """
  private static func replaceImageSources(in html: String, with dataURIBySrc: [String: String])
    -> String {
    html
  }

  private static func rewrittenImageOrSourceTag(
    _ tag: String,
    dataURIBySrc: [String: String]
  ) -> String {
    tag
  }
"""
    members = reorder.split_top_level_members(body)
    self.assertEqual(len(members), 2)
    self.assertIn("-> String", members[0])
    self.assertIn("html", members[0])
    self.assertIn("rewrittenImageOrSourceTag", members[1])

  def test_requirement_funcs_without_bodies_do_not_merge(self) -> None:
    body = """
  associatedtype Destination: View

  @MainActor @ViewBuilder func destination(userSession: SignedInUserSession) -> Destination

  var allowsLandscapeOrientation: Bool { get }

  func applyOpen(to path: inout [AppRoute])

  func isEnclosingFolder(
    forFilePath filePath: String,
    owner: String,
    repo: String,
    branch: String?
  ) -> Bool
"""
    members = reorder.split_top_level_members(body)
    self.assertEqual(len(members), 5)
    names = [reorder.member_name(m) for m in members]
    self.assertEqual(
      names,
      [
        "destination",
        "destination",
        "allowslandscapeorientation",
        "applyopen",
        "isenclosingfolder",
      ],
    )

  def test_protocol_body_puts_associatedtype_then_vars_then_funcs(self) -> None:
    body = """
  associatedtype Destination: View

  @MainActor @ViewBuilder func destination(userSession: SignedInUserSession) -> Destination

  var allowsLandscapeOrientation: Bool { get }

  func applyOpen(to path: inout [AppRoute])

  func isEnclosingFolder(
    forFilePath filePath: String,
    owner: String,
    repo: String,
    branch: String?
  ) -> Bool
"""
    reordered = reorder.reorder_plain_type_body(body, is_enum=False)
    at = reordered.index("associatedtype Destination")
    var_at = reordered.index("var allowsLandscapeOrientation")
    apply_at = reordered.index("func applyOpen")
    dest_at = reordered.index("@MainActor @ViewBuilder func destination")
    folder_at = reordered.index("func isEnclosingFolder")
    self.assertLess(at, var_at)
    self.assertLess(var_at, apply_at)
    self.assertLess(apply_at, dest_at)
    self.assertLess(dest_at, folder_at)


class MultilineStringReorderTests(unittest.TestCase):
  def test_graphql_mutation_braces_do_not_split_static_let(self) -> None:
    body = """
  public typealias Request = GitHubGraphQLIDRequest

  private static let mutation = \"\"\"
  mutation($id: ID!) {
    convertPullRequestToDraft(input: { pullRequestId: $id }) {
      clientMutationId
    }
  }
  \"\"\"

  public let request: Request

  public init(pullRequestId: String) {
    request = .init(query: Self.mutation, variables: .init(id: pullRequestId))
  }

  public struct Data: Decodable, Sendable {
    public let convertPullRequestToDraft: String?
  }
"""
    members = reorder.split_top_level_members(body)
    self.assertEqual(len(members), 5)
    mutation = next(m for m in members if "static let mutation" in m)
    self.assertIn("convertPullRequestToDraft", mutation)
    self.assertIn('"""', mutation)
    reordered = reorder.reorder_plain_type_body(body, is_enum=False)
    data_at = reordered.index("public struct Data")
    alias_at = reordered.index("public typealias Request")
    mutation_at = reordered.index("private static let mutation")
    request_at = reordered.index("public let request")
    init_at = reordered.index("public init(pullRequestId:")
    self.assertLess(data_at, alias_at)
    self.assertLess(alias_at, mutation_at)
    self.assertLess(mutation_at, request_at)
    self.assertLess(request_at, init_at)

  def test_html_func_inside_triple_quote_does_not_become_member(self) -> None:
    body = """
  static func makeDocument() -> String {
    \"\"\"
    <script>
    function boot() {
      return 1;
    }
    </script>
    \"\"\"
  }

  static let marker = true
"""
    members = reorder.split_top_level_members(body)
    self.assertEqual(len(members), 2)
    self.assertIn("function boot()", members[0])
    self.assertIn("static let marker", members[1])
    reordered = reorder.reorder_plain_type_body(body, is_enum=False)
    self.assertLess(reordered.index("static let marker"), reordered.index("static func makeDocument"))
    self.assertIn("function boot()", reordered)

  def test_file_layout_still_runs_when_preview_has_triple_quote(self) -> None:
    text = """\
import SwiftUI

#Preview {
  let diffString = \"\"\"
  @@ -1 +1 @@
  -a
  +b
  \"\"\"
  Text(diffString)
}

private struct Helper {
  var value = 1
}

struct PatchView: View {
  var body: some View { Text("hi") }
}
"""
    reordered = reorder.reorder_file_layout(text, Path("PatchView.swift"))
    helper_at = reordered.index("private struct Helper")
    view_at = reordered.index("struct PatchView")
    preview_at = reordered.index("#Preview")
    self.assertLess(helper_at, view_at)
    self.assertLess(view_at, preview_at)
    self.assertIn('"""', reordered)

  def test_raw_string_regex_does_not_split_or_hoist_members(self) -> None:
    body = """
  private static let quotedAttributeRegex: NSRegularExpression =
    // swiftlint:disable:next force_try
    try! NSRegularExpression(pattern: #\"\\b([a-zA-Z_:][-a-zA-Z0-9_:.]*)=([\"'])(.*?)\\2\"#)

  static func containsInlinedImages(_ html: String) -> Bool {
    true
  }

  private static func sniffedMimeType(for data: Data) -> String? {
    nil
  }
"""
    members = reorder.split_top_level_members(body)
    self.assertEqual(len(members), 3)
    self.assertIn("quotedAttributeRegex", members[0])
    self.assertIn("containsInlinedImages", members[1])
    self.assertIn("sniffedMimeType", members[2])
    reordered = reorder.reorder_plain_type_body(body, is_enum=False)
    self.assertLess(
      reordered.index("quotedAttributeRegex"),
      reordered.index("containsInlinedImages"),
    )
    self.assertLess(
      reordered.index("containsInlinedImages"),
      reordered.index("sniffedMimeType"),
    )

  def test_process_file_reorders_graphql_endpoint(self) -> None:
    original = """\
public struct ConvertPullRequestToDraftEndpoint: GitHubGraphQLEndpoint {
  public let request: Request

  public init(pullRequestId: String) {
    request = .init(query: Self.mutation, variables: .init(id: pullRequestId))
  }

  private static let mutation = \"\"\"
  mutation($id: ID!) {
    convertPullRequestToDraft(input: { pullRequestId: $id }) {
      clientMutationId
    }
  }
  \"\"\"

  public typealias Request = GitHubGraphQLIDRequest
}
"""
    with tempfile.TemporaryDirectory() as tmp:
      path = Path(tmp) / "ConvertPullRequestToDraftEndpoint.swift"
      path.write_text(original)
      reorder.process_file(path, include_views=True, include_non_views=True)
      text = path.read_text()
      self.assertLess(text.index("typealias Request"), text.index("static let mutation"))
      self.assertLess(text.index("static let mutation"), text.index("let request"))
      self.assertLess(text.index("let request"), text.index("init(pullRequestId:"))
      self.assertIn("convertPullRequestToDraft(input:", text)


class CheckModeTests(unittest.TestCase):
  def test_check_exits_1_without_writing_when_reorder_needed(self) -> None:
    original = """\
struct Sample {
  func b() {}
  func a() {}
}
"""
    with tempfile.TemporaryDirectory() as tmp:
      path = Path(tmp) / "Sample.swift"
      path.write_text(original)
      self.assertEqual(reorder.main(["--check", str(path)]), 1)
      self.assertEqual(path.read_text(), original)

  def test_check_exits_0_when_already_ordered(self) -> None:
    ordered = """\
struct Sample {
  func a() {}

  func b() {}
}
"""
    with tempfile.TemporaryDirectory() as tmp:
      path = Path(tmp) / "Sample.swift"
      path.write_text(ordered)
      self.assertEqual(reorder.main(["--check", str(path)]), 0)
      self.assertEqual(path.read_text(), ordered)

  def test_dry_run_prints_without_writing(self) -> None:
    original = """\
struct Sample {
  func b() {}
  func a() {}
}
"""
    with tempfile.TemporaryDirectory() as tmp:
      path = Path(tmp) / "Sample.swift"
      path.write_text(original)
      self.assertEqual(reorder.main(["--dry-run", str(path)]), 0)
      self.assertEqual(path.read_text(), original)


if __name__ == "__main__":
  unittest.main()
