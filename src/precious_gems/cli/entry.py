"""CLI entrypoint for precious_gems."""

import argparse
import sys
import time
from pathlib import Path
from typing import List, Optional

from ..browser import DEFAULT_CDP_URL, is_target_closed_error
from ..client import GeminiClient
from ..storage import (
    Catalog,
    CatalogItem,
    Manifest,
    detect_order_jumps,
    normalize_title,
    parse_size,
)
from tqdm import tqdm


def cmd_export(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir)
    manifest = Manifest(output_dir)

    target = args.target.strip()
    conv_id = target.split("/app/")[-1].split("?")[0].split("#")[0] if "/app/" in target else target

    if not args.force and manifest.has_conversation(conv_id):
        print(f"Skipping {conv_id}: already present in manifest at {output_dir}", flush=True)
        return 0

    max_att_size = (
        parse_size(args.max_attachment_size) if getattr(args, "max_attachment_size", None) else None
    )

    print(f"Connecting to Chrome CDP at {args.cdp_url}...", flush=True)
    with GeminiClient(cdp_url=args.cdp_url, output_dir=output_dir) as client:
        print(
            f"Exporting conversation {conv_id} (format={'html' if args.html else 'json'})...",
            flush=True,
        )
        record = client.export_conversation(
            conversation_id=conv_id,
            use_html_fallback=args.html,
            max_attachment_size=max_att_size,
        )
        catalog = Catalog(output_dir)
        catalog.update([CatalogItem(id=record.id, title=record.title, url=record.url)])
        catalog.save()
        out_name = "conversation.html" if record.format == "html" else "conversation.json"
        print(
            f"[OK] {record.id}: '{record.title}' -> {record.export_filename}/{out_name} ({record.size_bytes} bytes)",
            flush=True,
        )
    return 0


def cmd_raw(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    target = args.target.strip()
    conv_id = target.split("/app/")[-1].split("?")[0].split("#")[0] if "/app/" in target else target
    slug = conv_id.removeprefix("c_")

    print(f"Connecting to Chrome CDP at {args.cdp_url}...", flush=True)
    with GeminiClient(cdp_url=args.cdp_url, output_dir=output_dir) as client:
        print(f"Fetching raw conversation {slug}...", flush=True)
        raw_text = client.get_raw_conversation(slug)
        out_file = output_dir / f"{slug}_raw.txt"
        out_file.write_text(raw_text, encoding="utf-8")
        print(f"[OK] {slug} -> {out_file} ({len(raw_text.encode('utf-8'))} bytes)", flush=True)
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir)
    catalog = Catalog(output_dir)

    if args.max_scrolls is not None and not args.html:
        print(
            "[WARNING] --max-scrolls is ignored unless --html is specified.",
            file=sys.stderr,
            flush=True,
        )
    max_scrolls = args.max_scrolls if args.max_scrolls is not None else 35

    print(f"Connecting to Chrome CDP at {args.cdp_url}...", flush=True)
    with GeminiClient(cdp_url=args.cdp_url, output_dir=output_dir) as client:
        print("Scanning conversations...", flush=True)
        conversations = client.list_conversations(
            use_html_fallback=args.html,
            max_scrolls=max_scrolls,
        )
        catalog_items = [CatalogItem(id=c.id, title=c.title, url=c.url) for c in conversations]
        catalog.update(catalog_items)
        catalog.save()
        print(
            f"Discovered and cached {len(catalog.items)} conversations -> {catalog.catalog_path}",
            flush=True,
        )
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir)
    catalog = Catalog(output_dir)

    if args.max_scrolls is not None and not args.html:
        print(
            "[WARNING] --max-scrolls is ignored unless --html is specified.",
            file=sys.stderr,
            flush=True,
        )
    max_scrolls = args.max_scrolls if args.max_scrolls is not None else 35

    print(f"Connecting to Chrome CDP at {args.cdp_url}...", flush=True)
    with GeminiClient(cdp_url=args.cdp_url, output_dir=output_dir) as client:
        manifest = getattr(client, "manifest", None) or Manifest(output_dir)
        if args.from_cache:
            if not catalog.items:
                print(
                    f"No cached conversations found in {catalog.catalog_path}. Run scan first or omit --from-cache.",
                    flush=True,
                )
                return 1
            print(f"Using cached catalog: {len(catalog.items)} conversations.", flush=True)
            targets = catalog.items
        else:
            print("Enumerating conversations...", flush=True)
            discovered = client.list_conversations(
                use_html_fallback=args.html,
                max_scrolls=max_scrolls,
            )
            catalog_items = [CatalogItem(id=c.id, title=c.title, url=c.url) for c in discovered]
            catalog.update(catalog_items)
            catalog.save()
            print(
                f"Discovered {len(discovered)} conversations (catalog total: {len(catalog.items)}).",
                flush=True,
            )
            targets = catalog.items

        # Detect order jumps to identify conversations that were updated
        previous_order = manifest.order or catalog.conversation_ids
        current_order = [item.id for item in targets]
        jumped_ids = detect_order_jumps(previous_order, current_order)
        manifest.set_order(current_order)

        count = 0
        for idx, item in enumerate(targets):
            if args.limit and count >= args.limit:
                print(f"Reached limit of {args.limit} conversations.", flush=True)
                break

            old_pos = previous_order.index(item.id) if item.id in previous_order else -1
            new_pos = idx

            # Classify item: NEW, UPDATE, FORCE, or SKIP
            if not manifest.has_conversation(item.id):
                action = "NEW"
                reason = "new conversation"
            else:
                record = manifest.get_record(item.id) or {}
                if record.get("deleted_upstream"):
                    # Silently skip upstream deleted conversations
                    continue

                old_title = normalize_title(record.get("title", ""))
                new_title = normalize_title(item.title)
                if old_title and old_title != new_title:
                    action = "UPDATE"
                    reason = f"title changed from '{old_title}'"
                elif item.id in jumped_ids:
                    action = "UPDATE"
                    reason = f"jumped pos {old_pos} -> {new_pos}"
                elif args.force:
                    action = "FORCE"
                    reason = "force re-export"
                else:
                    print(f"Verifying integrity for {item.id}...", flush=True)
                    is_intact, integrity_reason = manifest.verify_conversation_integrity(item.id)
                    if not is_intact:
                        action = "UPDATE"
                        reason = f"integrity check failed ({integrity_reason})"
                    else:
                        action = "SKIP"
                        reason = f"unchanged relative order (pos {new_pos})"

            if action == "SKIP":
                continue

            max_att_size = (
                parse_size(args.max_attachment_size)
                if getattr(args, "max_attachment_size", None)
                else None
            )
            print(f"[{action}: {reason}] [{count + 1}] {item.id}: '{item.title}'...", flush=True)
            try:
                record = client.export_conversation(
                    conversation_id=item.id,
                    title=item.title,
                    use_html_fallback=args.html,
                    max_attachment_size=max_att_size,
                )
                out_name = "conversation.html" if record.format == "html" else "conversation.json"
                print(
                    f"[OK] {record.id} -> {record.export_filename}/{out_name} ({record.size_bytes} bytes)",
                    flush=True,
                )
                count += 1
            except Exception as err:
                if is_target_closed_error(err):
                    raise
                print(f"[ERROR] Failed exporting {item.id}: {err}", flush=True)
                raise

            if args.delay > 0:
                time.sleep(args.delay)

        # Ensure updated order is saved
        manifest.save()
        print(
            f"Sync complete. Total newly exported/updated: {count}. Archive located at: {output_dir}",
            flush=True,
        )
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir)
    manifest = Manifest(output_dir)
    catalog = Catalog(output_dir)

    if args.all:
        if not catalog.items:
            print(
                f"No cached catalog found in {output_dir}. Run `scan` to discover all conversations.",
                flush=True,
            )
            return 0

        previous_order = manifest.order
        current_order = catalog.conversation_ids
        jumped_ids = detect_order_jumps(previous_order, current_order) if previous_order else set()

        print(f"Discovered conversations in catalog ({len(catalog.items)} total):", flush=True)
        for idx, item in enumerate(catalog.items):
            if not manifest.has_conversation(item.id):
                status = "[Pending]"
            else:
                rec = manifest.get_record(item.id) or {}
                if rec.get("title") and normalize_title(rec.get("title")) != normalize_title(
                    item.title
                ):
                    status = "[Needs Update (title changed)]"
                elif item.id in jumped_ids:
                    old_pos = (
                        previous_order.index(item.id)
                        if (previous_order and item.id in previous_order)
                        else "?"
                    )
                    status = f"[Needs Update (pos {old_pos} -> {idx})]"
                else:
                    status = "[Exported]"
            print(f" {status} {item.id}: {item.title}", flush=True)
        return 0

    if not manifest.conversations:
        print(f"No exported conversations found in {output_dir}", flush=True)
        return 0

    print(
        f"Exported conversations in {output_dir} ({len(manifest.conversations)} total):", flush=True
    )
    for cid, record in manifest.conversations.items():
        title = record.get("title", "Untitled")
        base_name = record.get("export_filename", "")
        fmt = record.get("format", "json")
        conv_file = "conversation.html" if fmt == "html" else "conversation.json"
        size = record.get("size_bytes", 0)
        pos = manifest.order.index(cid) if cid in manifest.order else -1
        print(f" - {cid} (pos {pos}): {title} [{base_name}/{conv_file}] ({size} bytes)", flush=True)
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir)

    clean_ids: List[str] = []
    for raw_target in args.targets:
        target = raw_target.strip()
        conv_id = (
            target.split("/app/")[-1].split("?")[0].split("#")[0] if "/app/" in target else target
        )
        conv_id = conv_id.removeprefix("c_")
        if conv_id and conv_id not in clean_ids:
            clean_ids.append(conv_id)

    if not clean_ids:
        print("No valid conversation IDs provided.", flush=True)
        return 1

    print(f"Connecting to Chrome CDP at {args.cdp_url}...", flush=True)
    with GeminiClient(cdp_url=args.cdp_url, output_dir=output_dir) as client:
        deleted_count = 0
        skipped_count = 0
        not_found_count = 0
        archived_deleted_count = 0

        pbar = tqdm(clean_ids, desc="Deleting conversations", unit="conv")
        for cid in pbar:
            pbar.set_postfix_str(cid)
            res = client.delete_conversation(
                conversation_id=cid,
                delete_archived=args.delete_archived,
                force=args.force,
            )
            st = res.get("status")
            if st == "deleted":
                deleted_count += 1
                if res.get("archived_deleted"):
                    archived_deleted_count += 1
            elif st == "skipped":
                skipped_count += 1
            elif st == "not_found":
                not_found_count += 1

        print("\n=== Deletion Summary ===", flush=True)
        print(f"Total processed:               {len(clean_ids)}", flush=True)
        print(f"Successfully deleted upstream: {deleted_count}", flush=True)
        print(f"Skipped (unarchived/outdated): {skipped_count}", flush=True)
        print(f"Not found on Gemini:           {not_found_count}", flush=True)
        if args.delete_archived:
            print(f"Local archives purged:         {archived_deleted_count}", flush=True)

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="precious-gems",
        description="Programmatic backup and export tool for Google Gemini conversations.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # export subcommand
    p_export = subparsers.add_parser("export", help="Export a single conversation by ID or URL.")
    p_export.add_argument("target", help="Conversation URL or ID to export.")
    p_export.add_argument(
        "--output-dir", default="./output", help="Directory to save exported files."
    )
    p_export.add_argument("--cdp-url", default=DEFAULT_CDP_URL, help="Chrome CDP debugging URL.")
    p_export.add_argument(
        "--force", action="store_true", help="Re-export even if already in manifest."
    )
    p_export.add_argument(
        "--html",
        action="store_true",
        help="Use legacy in-browser print DOM export producing HTML instead of direct backend JSON.",
    )
    p_export.add_argument(
        "--max-attachment-size",
        default=None,
        help="Maximum attachment size to download (e.g. '50MB', '500KB', '1000').",
    )
    p_export.set_defaults(func=cmd_export)

    # raw subcommand
    p_raw = subparsers.add_parser(
        "raw", help="Fetch and save raw batchexecute response text for a conversation."
    )
    p_raw.add_argument("target", help="Conversation URL or ID to fetch.")
    p_raw.add_argument(
        "--output-dir", default="./output", help="Directory to save raw response file."
    )
    p_raw.add_argument("--cdp-url", default=DEFAULT_CDP_URL, help="Chrome CDP debugging URL.")
    p_raw.set_defaults(func=cmd_raw)

    # scan subcommand
    p_scan = subparsers.add_parser("scan", help="Scan and cache all conversation IDs and titles.")
    p_scan.add_argument(
        "--output-dir", default="./output", help="Directory to save conversations.json cache."
    )
    p_scan.add_argument("--cdp-url", default=DEFAULT_CDP_URL, help="Chrome CDP debugging URL.")
    p_scan.add_argument(
        "--max-scrolls",
        type=int,
        default=None,
        help="Maximum sidebar scroll passes (only applies when --html is specified).",
    )
    p_scan.add_argument(
        "--html",
        action="store_true",
        help="Use legacy DOM sidebar scrolling instead of direct backend RPC.",
    )
    p_scan.set_defaults(func=cmd_scan)

    # sync subcommand
    p_sync = subparsers.add_parser("sync", help="Sync conversations from sidebar top-to-bottom.")
    p_sync.add_argument(
        "--output-dir", default="./output", help="Directory to save exported files."
    )
    p_sync.add_argument("--cdp-url", default=DEFAULT_CDP_URL, help="Chrome CDP debugging URL.")
    p_sync.add_argument(
        "--delay", type=float, default=0.5, help="Delay in seconds between conversation exports."
    )
    p_sync.add_argument(
        "--limit", type=int, default=None, help="Maximum number of conversations to export."
    )
    p_sync.add_argument(
        "--force", action="store_true", help="Re-export even if already in manifest."
    )
    p_sync.add_argument(
        "--html",
        action="store_true",
        help="Use legacy in-browser print DOM export producing HTML instead of direct backend JSON.",
    )
    p_sync.add_argument(
        "--from-cache",
        action="store_true",
        help="Sync using cached conversations.json instead of rescan.",
    )
    p_sync.add_argument(
        "--max-scrolls",
        type=int,
        default=None,
        help="Maximum sidebar scroll passes (only applies when --html is specified).",
    )
    p_sync.add_argument(
        "--max-attachment-size",
        default=None,
        help="Maximum attachment size to download (e.g. '50MB', '500KB', '1000').",
    )
    p_sync.set_defaults(func=cmd_sync)

    # list subcommand
    p_list = subparsers.add_parser("list", help="List exported conversations in the local archive.")
    p_list.add_argument("--output-dir", default="./output", help="Directory of the manifest.")
    p_list.add_argument(
        "--all",
        "--catalog",
        dest="all",
        action="store_true",
        help="List all cataloged conversations with sync status.",
    )
    p_list.set_defaults(func=cmd_list)

    # delete subcommand
    p_delete = subparsers.add_parser(
        "delete", help="Delete conversation(s) upstream on Gemini by ID or URL."
    )
    p_delete.add_argument(
        "targets", nargs="+", help="One or more conversation IDs or URLs to delete."
    )
    p_delete.add_argument(
        "--output-dir", default="./output", help="Directory of the manifest and archives."
    )
    p_delete.add_argument("--cdp-url", default=DEFAULT_CDP_URL, help="Chrome CDP debugging URL.")
    p_delete.add_argument(
        "--delete-archived",
        action="store_true",
        help="Also delete local exported directory and manifest/catalog entry.",
    )
    p_delete.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Force upstream deletion even if conversation is not archived or up-to-date locally.",
    )
    p_delete.set_defaults(func=cmd_delete)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n[INFO] Interrupted by user (Ctrl+C). Exiting.", file=sys.stderr, flush=True)
        return 130
    except Exception as err:
        if is_target_closed_error(err):
            print(
                "[ERROR] Browser tab or page was closed by user. Exiting.",
                file=sys.stderr,
                flush=True,
            )
            return 1
        raise


if __name__ == "__main__":
    sys.exit(main())
