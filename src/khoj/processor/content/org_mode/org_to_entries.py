import logging
from pathlib import Path
from typing import Dict, List, Tuple

from khoj.database.models import Entry as DbEntry
from khoj.database.models import KhojUser
from khoj.processor.content.org_mode import orgnode
from khoj.processor.content.org_mode.orgnode import Orgnode
from khoj.processor.content.text_to_entries import TextToEntries
from khoj.utils import state
from khoj.utils.helpers import timer
from khoj.utils.rawconfig import Entry

logger = logging.getLogger(__name__)


class OrgToEntries(TextToEntries):
    def __init__(self):
        super().__init__()

    # Define Functions
    def process(
        self, files: dict[str, str] = None, full_corpus: bool = True, user: KhojUser = None, regenerate: bool = False
    ) -> Tuple[int, int]:
        if not full_corpus:
            deletion_file_names = set([file for file in files if files[file] == ""])
            files_to_process = set(files) - deletion_file_names
            files = {file: files[file] for file in files_to_process}
        else:
            deletion_file_names = None

        # Extract Entries from specified Org files
        with timer("Extract entries from specified Org files", logger):
            current_entries = self.extract_org_entries(files)

        with timer("Split entries by max token size supported by model", logger):
            current_entries = self.split_entries_by_max_tokens(current_entries, max_tokens=256)

        # Identify, mark and merge any new entries with previous entries
        with timer("Identify new or updated entries", logger):
            num_new_embeddings, num_deleted_embeddings = self.update_embeddings(
                current_entries,
                DbEntry.EntryType.ORG,
                DbEntry.EntrySource.COMPUTER,
                "compiled",
                logger,
                deletion_file_names,
                user,
                regenerate=regenerate,
            )

        return num_new_embeddings, num_deleted_embeddings

    @staticmethod
    def extract_org_entries(org_files: dict[str, str], index_heading_entries: bool = False) -> List[Entry]:
        "Extract entries from specified Org files"
        with timer("Parse entries from org files into OrgNode objects", logger):
            entry_nodes, file_to_entries = OrgToEntries.extract_org_nodes(org_files)

        with timer("Convert OrgNodes into list of entries", logger):
            return OrgToEntries.convert_org_nodes_to_entries(entry_nodes, file_to_entries, index_heading_entries)

    @staticmethod
    def extract_org_nodes(org_files: dict[str, str]) -> Tuple[List[Orgnode], Dict[Orgnode, str]]:
        "Extract org nodes from specified org files"
        entry_nodes: List[Orgnode] = []
        entry_to_file_map: List[Tuple[Orgnode, str]] = []
        for org_file in org_files:
            org_content = org_files[org_file]
            entry_nodes, entry_to_file_map = OrgToEntries.process_single_org_file(
                org_content, org_file, entry_nodes, entry_to_file_map
            )

        return entry_nodes, dict(entry_to_file_map)

    @staticmethod
    def process_single_org_file(
        org_content: str,
        org_file: str,
        entries: List[Orgnode],
        entry_to_file_map: List[Tuple[Orgnode, str]],
    ) -> Tuple[List[Orgnode], List[Tuple[Orgnode, str]]]:
        # Process single org file. The org parser assumes that the file is a single org file and reads it from a buffer.
        # We'll split the raw content of this file by new line to mimic the same behavior.
        try:
            org_file_entries = orgnode.makelist(org_content, org_file)
            entry_to_file_map += zip(org_file_entries, [org_file] * len(org_file_entries))
            entries.extend(org_file_entries)
        except Exception as e:
            logger.error(f"Unable to process file: {org_file}. Skipped indexing it.\nError; {e}", exc_info=True)

        return entries, entry_to_file_map

    @staticmethod
    def convert_org_nodes_to_entries(
        parsed_entries: List[orgnode.Orgnode], entry_to_file_map, index_heading_entries=False
    ) -> List[Entry]:
        "Convert Org-Mode nodes into list of Entry objects"
        entries: List[Entry] = []
        for parsed_entry in parsed_entries:
            if not parsed_entry.hasBody and not index_heading_entries:
                # Ignore title notes i.e notes with just headings and empty body
                continue

            todo_str = f"{parsed_entry.todo} " if parsed_entry.todo else ""

            # Prepend ancestor headings, filename as top heading to entry for context
            ancestors_trail = " / ".join(parsed_entry.ancestors) or Path(entry_to_file_map[parsed_entry])
            if parsed_entry.heading:
                heading = f"* Path: {ancestors_trail}\n** {todo_str}{parsed_entry.heading}."
            else:
                heading = f"* Path: {ancestors_trail}."

            compiled = heading
            if state.verbose > 2:
                logger.debug(f"Title: {heading}")

            if parsed_entry.tags:
                tags_str = " ".join(parsed_entry.tags)
                compiled += f"\t {tags_str}."
                if state.verbose > 2:
                    logger.debug(f"Tags: {tags_str}")

            if parsed_entry.closed:
                compiled += f'\n Closed on {parsed_entry.closed.strftime("%Y-%m-%d")}.'
                if state.verbose > 2:
                    logger.debug(f'Closed: {parsed_entry.closed.strftime("%Y-%m-%d")}')

            if parsed_entry.scheduled:
                compiled += f'\n Scheduled for {parsed_entry.scheduled.strftime("%Y-%m-%d")}.'
                if state.verbose > 2:
                    logger.debug(f'Scheduled: {parsed_entry.scheduled.strftime("%Y-%m-%d")}')

            if parsed_entry.hasBody:
                compiled += f"\n {parsed_entry.body}"
                if state.verbose > 2:
                    logger.debug(f"Body: {parsed_entry.body}")

            if compiled:
                entries.append(
                    Entry(
                        compiled=compiled,
                        raw=f"{parsed_entry}",
                        heading=f"{heading}",
                        file=f"{entry_to_file_map[parsed_entry]}",
                    )
                )

        return entries
