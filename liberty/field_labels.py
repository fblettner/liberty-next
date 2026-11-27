"""The words a consultant reads in the forms built from the configuration models.

A form of the builders is drawn from a model's JSON schema: its label was the field's name
("Dd", "Rules Values", "Disable On Add") and its hint the developer's documentation of the field,
shown raw. This catalogue gives every field of those forms a **label** in business words and **one
short sentence** of help; :func:`apply_labels` writes both into the schema the builders receive
(``GET /admin/config/schema``) and keeps the model's own description — the long text, with its
examples and its notation — behind the form's "more" (``x_more``).

Why a catalogue and not ``title=`` on each field: the models' descriptions are documentation other
readers use as they are; here every word of the forms sits in one file, read in one sitting, and a
field added to a model without an entry fails ``tests/test_field_labels.py``.

The hints are in English (the generated forms are not translated).

An entry is ``"field": ("Label", "One short sentence.")`` under the class that DECLARES the field:
a model inherits the entries of its bases (``_ActionBase`` gives every action its id and label).
"""

from __future__ import annotations

import re
from typing import Any, get_args, get_origin

from pydantic import BaseModel

#: The longest a hint may be: one sentence a consultant reads at a glance.
MAX_HINT = 130
#: The longest a label may be: it sits on one line above its field.
MAX_LABEL = 34
#: A model's own description shorter than this says nothing the hint does not: no "more".
MORE_FROM = 110

Label = tuple[str, str]

_KEEP: Label = ("Keep on upgrade", "An upgrade of the application leaves this one exactly as you saved it.")
_TRANSLATIONS: Label = ("Translations", "The label in the other languages.")

LABELS: dict[str, dict[str, Label]] = {
    # ── pools ────────────────────────────────────────────────────────────────────────────────
    "PoolConfig": {
        "url": ("Address", "Where the database or the server is: its type, host, port and name."),
        "password": ("Password", "The account's password, kept apart from the address and stored encrypted."),
        "dialect": ("Database type", "Oracle, PostgreSQL… Leave blank to read it from the address."),
        "signing_key": ("Signing key", "The private key that signs each call made in the name of the signed-in user."),
        "certificate": ("Certificate", "The certificate that goes with the signing key, when the server asks for one."),
        "verify_ssl": ("Check the server's certificate", "Turn off only for a test server with a self-signed certificate."),
        "schemas": ("Schema names", "The real schema behind each schema name the queries use."),
        "dblinks": ("Database links", "The database link behind each link name the queries use; blank reads locally."),
        "pool_size": ("Connections kept open", "How many connections stay open, ready to be used."),
        "max_overflow": ("Extra connections", "How many more connections may be opened when the server is busy."),
        "pool_pre_ping": ("Test before use", "Check that a connection still answers before using it."),
        "pool_recycle": ("Renew after (seconds)", "A connection older than this is replaced; -1 keeps it for ever."),
        "echo": ("Log every statement", "Write every statement to the server log. For a diagnosis only."),
        "jit": ("Let PostgreSQL compile queries", "PostgreSQL only. Off is faster for screens and searches; on leaves the database's own setting."),
        "max_rows": ("Rows read at most", "How many rows a screen reads from this database unless it says otherwise."),
        "arraysize": ("Rows per round trip", "Oracle only: how many rows are fetched at a time. Raise it for large tables."),
        "trim_strings": ("Trim trailing spaces", "Remove the spaces a fixed-width text column adds at the end of its values."),
        "coalesce_nulls": ("Fill empty values", "Write a space or a zero where a value is left empty, for columns that refuse empty."),
        "debug_sql": ("Log statements and values", "Write each statement with its values to the server log. For a diagnosis only."),
    },
    # ── connectors (SQL) ─────────────────────────────────────────────────────────────────────
    "SqlConnectorConfig": {
        "type": ("Type", "A database connector."),
        "pool": ("Database", "The database this application's queries run on."),
        "pools": ("Other databases", "The other databases a user may switch to: same screens, another environment."),
        "licensed": ("Needs a licence", "The application is loaded only when the installation holds a valid licence key."),
        "applications": ("Serves the applications", "The licensed applications this connector serves: it is loaded when the licence covers one of them. Empty: it stands alone."),
        "max_rows": ("Rows read at most", "How many rows a screen of this application reads unless it says otherwise."),
        "dd_prefix": ("Column prefix length", "How many leading characters of a column name are the table's prefix; 0 for none."),
        "show_in_switcher": ("Show in the application list", "Offer this application in the list at the top of the menu."),
        "home": ("Home page", "The menu entry opened when the application is picked."),
        "home_by_role": ("Home page per role", "A different home page for the users of a role."),
        "tables": ("Tables", "The tables of this application, each with its read, change, add and delete."),
        "queries": ("Queries", "The queries that are not a table's read or save."),
        "sequences": ("Next-number queries", "The queries that give the next number of a sequence."),
        "lookups": ("List queries", "The queries that feed a dropdown list."),
    },
    "TableDef": {
        "name": ("Name", "The table's short name, used to name its queries."),
        "label": ("Label", "The name shown in the lists."),
        "description": ("Description", "What this table holds, in a sentence."),
        "get": ("Read", "The statement that reads the rows."),
        "put": ("Change", "The statement that saves a changed row."),
        "post": ("Add", "The statement that adds a row."),
        "delete": ("Delete", "The statement that deletes a row."),
    },
    "CrudSlot": {
        "sql": ("Statement", "The SQL statement; a value it takes is written :NAME."),
        "writable": ("Changes data", "Tick for a statement that adds, changes or deletes rows."),
        "params": ("Parameters", "The values the statement takes, each with its label and default."),
    },
    "ParamDef": {
        "name": ("Name", "The parameter's name, as written in the statement or the address."),
        "label": ("Label", "The label of the field where the value is typed."),
        "default": ("Default value", "The value used when none is given."),
        "help": ("Help", "What to put in it, in a sentence; shown under the field."),
        "example": ("Example", "An example value, shown with the help."),
        "choices": ("Allowed values", "The only values allowed, offered as a dropdown."),
        "required": ("Required", "A call without this value is refused before anything is sent."),
        "lookup": ("List to pick in", "A lookup of the dictionary: the value is picked in its list instead of typed."),
        "lookup_connector": ("Connector of the lookup", "The connector the lookup belongs to, when it is not this one."),
        "lookup_binds": ("What narrows the list", "A parameter of the lookup, set to another parameter here or to a value."),
    },
    "QueryDef": {
        "name": ("Name", "The query's name, unique in its application."),
        "sql": ("Statement", "The SQL statement; a value it takes is written :NAME."),
        "writable": ("Changes data", "Tick for a statement that adds, changes or deletes rows."),
        "params": ("Parameters", "The values the statement takes, each with its label and default."),
        "label": ("Label", "The name shown in the lists."),
        "description": ("Description", "What this query returns, in a sentence."),
    },
    # ── dictionary ───────────────────────────────────────────────────────────────────────────
    "DictionaryFile": {
        "default_language": ("Default language", "The language the labels are written in."),
        "entries": ("Items", "The dictionary items shared by every application."),
        "enums": ("Lists of values", "The fixed lists shared by every application."),
        "lookups": ("Lists from a table", "The lists read from a table, shared by every application."),
        "sequences": ("Next numbers", "The numbering rules shared by every application."),
        "connectors": ("Per application", "What each application adds to the shared dictionary."),
        "framework_enums": ("Framework lists", "The lists the builders themselves offer (formats, kinds of field…)."),
    },
    "DictionarySection": {
        "entries": ("Items", "The dictionary items of this application."),
        "enums": ("Lists of values", "The fixed lists of this application."),
        "lookups": ("Lists from a table", "The lists this application reads from a table."),
        "sequences": ("Next numbers", "The numbering rules of this application."),
    },
    "DictionaryEntry": {
        "label": ("Label", "The name shown above the column and the field."),
        "format": ("Format", "How the value is shown: text, number, date, yes / no…"),
        "decimals": ("Decimals", "How many decimal places the number has."),
        "rules": ("Kind of field", "What the field is: free text, a list, a yes / no box, a number given automatically…"),
        "rules_values": ("List or source", "What the kind of field uses: the list, the numbering rule, the value that means yes."),
        "false_value": ("Value for no", "What is written when the box is unticked; blank lets Liberty work it out."),
        "default": ("Default value", "The value a new record starts with."),
        "justify": ("Align when saving", "Pad the value on the left when it is saved, for codes stored right-aligned."),
        "size": ("Stored width", "How many characters the value takes when it is stored, for right-aligned codes."),
        "sensitive": ("Sensitive data", "Personal or confidential: left out of what the AI assistant is given."),
        "lookup_params": ("List parameters", "Fixed values given to the list's query, such as the code of the list to read."),
        "l": ("Translations", "The label in the other languages."),
        "override": _KEEP,
    },
    "EnumDef": {
        "label": ("Label", "The name of this list."),
        "values": ("Values", "Each code and what is shown in its place."),
        "override": _KEEP,
    },
    "EnumValue": {
        "value": ("Code", "The value stored in the database."),
        "label": ("Label", "What is shown in place of the code."),
        "l": _TRANSLATIONS,
    },
    "LookupDef": {
        "description": ("Description", "What this list holds, in a sentence."),
        "connector": ("Application", "The application whose query feeds the list; blank for the one that uses it."),
        "query": ("Query", "The query that returns the lines of the list."),
        "union": ("More sources", "Other queries whose lines are added to the list, from any database."),
        "value": ("Code column", "The column of the query that holds the stored code."),
        "label": ("Label column", "The column of the query shown in place of the code."),
        "group": ("Group", "Not used."),
        "key_columns": ("Key columns", "Other columns that tell two lines with the same code apart."),
        "return_params": ("Columns handed back", "Columns of the picked line that may fill other fields of the record."),
        "display_fields": ("Extra columns shown", "Other columns shown beside the code and the label in the dropdown."),
        "hide_value": ("Hide the code", "The code is an identifier nobody reads: the dropdown lists the label only. It is still what is saved."),
        "filter_fields": ("Columns to filter on", "The extra columns offered as quick filters at the top of the dropdown."),
        "override": _KEEP,
    },
    "LookupSource": {
        "connector": ("Application", "The application whose query is added; blank for the list's own."),
        "query": ("Query", "A query that returns the same columns as the list's own."),
    },
    "SequenceDef": {
        "description": ("Description", "What this numbering is for, in a sentence."),
        "connector": ("Application", "The application whose query gives the number; blank for the one that uses it."),
        "query": ("Query", "The query that returns the next number."),
        "dd_id": ("Dictionary item", "Number every column carrying this item automatically."),
        "params": ("Parameters", "The values the query takes; they come from the record being added."),
        "override": _KEEP,
    },
}

# ── menus ────────────────────────────────────────────────────────────────────────────────────
LABELS.update({
    "MenusFile": {
        "menus": ("Menus", "The menu of each application."),
    },
    "AppMenu": {
        "label": ("Application name", "The name shown for the application; blank shows its technical name."),
        "items": ("Entries", "The folders and the entries of the menu."),
    },
    "MenuItem": {
        "id": ("Id", "A short name for this entry, unique in its application."),
        "parent": ("Folder", "The folder this entry sits in; blank for the top of the menu."),
        "label": ("Label", "The name shown in the menu."),
        "l": _TRANSLATIONS,
        "icon": ("Icon", "The small picture shown before the label."),
        "type": ("What it opens", "A screen, a dashboard, a job, a page, a link… Blank makes a folder."),
        "connector": ("Application", "The application that holds what is opened; blank for this one."),
        "target": ("Opens", "The screen, dashboard, job, page or address this entry opens."),
        "params": ("Fixed values", "Values given to what is opened, such as a filter already filled in."),
        "screens": ("Screens the page works through", "For a page: the screens it reads and saves through; the entry gives their access too."),
        "roles": ("Only for the roles", "Show the entry to these roles only; blank shows it to whoever may open it."),
        "override": _KEEP,
    },
})

# ── screens ──────────────────────────────────────────────────────────────────────────────────
LABELS.update({
    "ScreensFile": {
        "screens": ("Screens", "The screens of each application."),
    },
    "Screen": {
        "id": ("Id", "A short name for this screen, unique in its application."),
        "label": ("Label", "The short name shown in the menu and on the tab."),
        "description": ("Description", "The longer title shown at the top of the page."),
        "l": ("Label translations", "The label in the other languages."),
        "description_l": ("Description translations", "The description in the other languages."),
        "connector": ("Application of the data", "The application that holds the queries; blank for this screen's own."),
        "read_query": ("Read", "The query that returns the rows shown."),
        "update_query": ("Save a change", "The query that saves a changed record."),
        "insert_query": ("Add", "The query that adds a record."),
        "delete_query": ("Delete", "The query that deletes a record."),
        "save_connector": ("Save through", "The application whose services save the record, when it is not the one that reads it."),
        "columns": ("Columns", "The columns of the screen: label, format, filter, what may be typed."),
        "column_groups": ("Related tables", "Tables holding one row per record, changed on this screen and saved with it."),
        "auto_load": ("Load on opening", "Read the rows as soon as the screen opens, without a click on Run."),
        "audit_table": ("History table", "Keep a copy of every change in this table, with who and when; blank keeps none."),
        "change_tracked": ("Track changes", "Record every change in a change package, to review it and send it to another environment."),
        "change_entity": ("Tracked as", "The name the changes of this screen are grouped under in a change package."),
        "post_apply": ("Steps after a transfer", "What must run on the other environment once this screen's changes are applied."),
        "max_rows": ("Rows read at most", "How many rows this screen reads; blank uses the application's limit."),
        "page_size": ("Rows per page", "How many rows a page of the grid shows; blank uses the installation's setting."),
        "key_columns": ("Key columns", "The columns that identify one record; blank uses the columns marked as key."),
        "editable": ("Edit in the grid", "Let the user change values directly in the grid."),
        "editable_when": ("Editable only when", "A record may be changed or deleted only while these conditions hold."),
        "uploadable": ("Import from a file", "Show the button that imports an Excel or CSV file."),
        "read_only": ("Read only", "Nothing can be added, changed or deleted from this screen."),
        "disable_add": ("No new record", "Existing records may be changed, none may be added."),
        "initial_group_by": ("Grouped by", "The columns the grid is grouped by when it opens."),
        "views": ("Saved layouts", "Layouts of the grid offered to every user: columns shown, order, grouping."),
        "summary": ("Summary view", "A view that counts the rows per group and opens a group on demand."),
        "value_diff": ("Before / after view", "Show, for a row holding a recorded change, each value before and after."),
        "treeview": ("Tree view", "Show the rows as a tree, each under its parent."),
        "chart_id": ("Chart", "The saved chart the Chart view opens with."),
        "export": ("Export to workbooks", "Export one Excel file per group and one sheet per sub-group."),
        "dialog": ("Record form", "The form that opens to add or change a record."),
        "actions": ("Buttons", "The buttons above the grid and what each one does."),
        "row_menu": ("Row menu", "The entries of the menu opened on a row and what each one does."),
        "on_insert": ("After adding", "What runs once a record has been added."),
        "on_update": ("After a change", "What runs once a record has been changed."),
        "on_delete": ("After deleting", "What runs once a record has been deleted."),
        "on_duplicate": ("After duplicating", "What runs once a record has been duplicated, in place of After adding."),
        "row_click_screen": ("A click opens the form of", "Another screen whose form opens when a row is clicked."),
        "row_click_connector": ("In the application", "The application of that screen; blank for this one."),
        "row_click_binds": ("Values handed over", "Which columns of the clicked row find the record in the other screen."),
        "row_click_route": ("A click opens the page", "A page of the application opened when a row is clicked."),
        "override": _KEEP,
    },
    "ColumnHint": {
        "name": ("Column", "The column of the query these settings are for."),
        "dd": ("Dictionary item", "The dictionary item that gives this column its label, format and list."),
        "key": ("Key", "This column identifies the record, alone or with the other key columns."),
        "label": ("Label", "The name shown above the column and the field; blank uses the dictionary's."),
        "hidden": ("Hidden", "Not shown at first; the user can show it from the Columns menu."),
        "format": ("Format", "How the value is shown: text, number, date, yes / no… Blank uses the dictionary's."),
        "align": ("Alignment", "Left, right or centred; blank follows the kind of value."),
        "width": ("Width", "The column's width in pixels; blank fits the content."),
        "wrap": ("Wrap long text", "Show a long text over several lines instead of cutting it."),
        "params_of": ("Parameters of the service in", "The column naming a service: its parameters become the fields of this one."),
        "card": ("On a phone", "Where the column goes on a card: in the heading, always shown, or never."),
        "drill": ("Link that runs", "Show the value as a link that runs this row-menu entry."),
        "colspan": ("Width in the form", "How many columns of the record form the field takes; blank is one."),
        "filter": ("Filter", "Offer this column in the filters above the grid."),
        "filter_period": ("Period shown at first", "For a date filter: the period the screen opens on."),
        "filter_from": ("Narrowed by", "Other filters whose value narrows the choices of this one."),
        "visible_when": ("Shown only when", "Show the column only while a filter has a given value."),
        "rules": ("Kind of field", "What the field is: free text, a list, a yes / no box… Blank uses the dictionary's."),
        "rules_values": ("List or source", "What the kind of field uses: the list, the numbering rule, the value that means yes."),
        "rules_when": ("Another kind when", "Make the field another kind while another column has a given value."),
        "required": ("Required", "The record cannot be saved while this field is empty."),
        "required_when": ("Required only when", "Required only while these conditions hold in the form."),
        "disabled": ("Read only", "The value is shown and cannot be changed."),
        "disabled_when": ("Read only when", "Read only while these conditions hold in the form."),
        "disable_on_add": ("Read only when adding", "Cannot be typed in a new record; it can be changed afterwards."),
        "disable_on_edit": ("Read only when changing", "Typed when the record is added; cannot be changed afterwards."),
        "justify": ("Align when saving", "Pad the value on the left when it is saved; blank does as the dictionary says."),
        "justify_from": ("Align as the item in", "The column naming the dictionary item whose alignment and width this value follows."),
        "default": ("Default value", "The value a new record starts with."),
        "default_when": ("Forced value when", "Set and lock the value while another column has a given value."),
        "write_when": ("Saved only when", "Save this column only while the condition holds; otherwise it is saved empty."),
        "hide_label": ("Code only", "For a list: show the code alone in the grid, without its description."),
        "lookup_param_binds": ("Values given to the list", "Values of the record that narrow the list, such as the application of the row."),
        "return_binds": ("Fills other fields", "When a line is picked, fill other fields of the record from it."),
        "sensitive": ("Sensitive data", "Left out of what the AI assistant is given; blank does as the dictionary says."),
        "group": ("Related table", "The related table this column is saved to; blank for the screen's own table."),
    },
})

# ── a column's conditions, the values handed from one thing to another ───────────────────────
LABELS.update({
    "FilterDep": {
        "source": ("Filter", "The other filter whose value narrows this one."),
        "column": ("Matched on", "The column of this list that is compared with that filter's value."),
    },
    "VisibleWhen": {
        "field": ("Column", "The column whose value is tested."),
        "value": ("Is one of", "The value, or the values, that make the condition hold."),
    },
    "FieldCondition": {
        "field": ("Field", "The field whose value is tested."),
        "value": ("Is one of", "The value, or the values, that make the condition hold."),
    },
    "RulesWhen": {
        "field": ("Column", "The column whose value decides."),
        "value": ("Is one of", "The value, or the values, for which this kind applies."),
        "rules": ("Kind of field", "What the field is then; blank for free text."),
        "rules_values": ("List or source", "What that kind uses: the list, the numbering rule, the value that means yes."),
        "false_value": ("Value for no", "What is written when the box is unticked; blank lets Liberty work it out."),
        "lookup_param_binds": ("Values given to the list", "Values of the record that narrow the list in this case."),
        "return_binds": ("Fills other fields", "When a line is picked in this case, fill other fields from it."),
    },
    "ParamBind": {
        "param": ("Parameter", "The value of what is called that receives it."),
        "value": ("Fixed value", "A value written here, the same every time."),
        "source": ("Taken from", "The column, the field or the earlier step the value is read from."),
        "default": ("If empty", "The value used when the one read is empty."),
    },
    "ReturnBind": {
        "param": ("Column of the list", "The column of the picked line whose value is copied."),
        "column": ("Field filled", "The field of the record that receives it."),
    },
    "DefaultWhen": {
        "field": ("Column", "The column whose value decides."),
        "value": ("Is one of", "The value, or the values, for which this applies."),
        "default": ("Value set", "The value given to this column then."),
        "lock": ("Locked", "The value cannot be changed while the condition holds; untick to let the user change it."),
    },
    "ColumnGroup": {
        "id": ("Id", "A short name for this related table, unique in the screen."),
        "label": ("Label", "The name shown in this builder."),
        "connector": ("Application", "The application that holds its queries; blank for the screen's."),
        "update_query": ("Save a change", "The query that saves the related row."),
        "insert_query": ("Add", "The query that adds the related row when there is none yet."),
        "delete_query": ("Delete", "The query that deletes the related row when the record is deleted."),
        "key_columns": ("Key columns", "The columns that show the related row exists: filled means change, empty means add."),
        "param_binds": ("Link to the record", "Which columns of the record identify its related row."),
        "insert_on_add": ("Always add", "Add the related row with every new record, even when none of its fields is filled."),
    },
})

# ── a screen's views ─────────────────────────────────────────────────────────────────────────
LABELS.update({
    "ScreenView": {
        "name": ("Name", "The name shown in the list of layouts."),
        "default": ("Opens with it", "The grid opens with this layout."),
        "columns": ("Columns shown", "The columns shown, in order; blank shows them all."),
        "sort": ("Sorted by", "The columns the rows are sorted by, in order."),
        "group_by": ("Grouped by", "The columns the rows are grouped by, in order."),
        "page_size": ("Rows per page", "How many rows a page shows in this layout; blank uses the screen's."),
    },
    "ScreenViewSort": {
        "column": ("Column", "The column to sort by."),
        "desc": ("Largest first", "Sort from the largest to the smallest, or from Z to A."),
    },
    "ScreenSummary": {
        "dimensions": ("Grouped by", "The columns the rows are counted by, in order."),
        "count_label": ("Heading of the count", "The heading of the column that shows how many rows a group holds."),
    },
    "ScreenSummaryDimension": {
        "column": ("Column", "The column to group by."),
        "bucket": ("By period", "For a date: group by day, month or year instead of by exact value."),
    },
    "ScreenValueDiff": {
        "sql_column": ("Column of the statement", "The column that holds the recorded statement to read the values from."),
        "operation_column": ("Column of the operation", "The column that says add, change or delete; blank reads it from the statement."),
    },
    "ScreenTreeview": {
        "parent": ("Parent column", "The column that holds the id of the row above; empty puts the row at the top."),
        "child": ("Id column", "The column that holds the row's own id."),
        "label": ("Text shown", "The column shown for each line, or a text with columns in braces: {NAME} ({CODE})."),
        "order_by": ("Ordered by", "The columns the lines of one level are sorted by; blank sorts on the text."),
    },
    "WorkbookExport": {
        "split_by": ("One file per", "The column whose every value gives one Excel file; blank gives a single file."),
        "sheets": ("Sheets", "The sheets of each file, in order."),
        "file_name_template": ("File name", "The name of each file; it may hold the value the file is for."),
        "archive_name": ("Name of the zip", "The name of the zip downloaded when there are several files."),
    },
    "SheetSpec": {
        "name": ("Sheet name", "The name of the sheet; it may hold the value the file or the sheet is for."),
        "connector": ("Application", "The application that holds the sheet's query; blank for the screen's."),
        "query": ("Query", "The query that returns the rows of the sheet."),
        "split_by": ("One sheet per", "The column whose every value gives one sheet; blank gives a single sheet."),
        "param_binds": ("Values given to the query", "What the query receives, such as the value the file is for."),
    },
})

# ── the record form ──────────────────────────────────────────────────────────────────────────
LABELS.update({
    "ScreenDialog": {
        "title": ("Title", "The title of the form; blank uses the screen's label."),
        "tabs": ("Tabs", "The tabs of the form, in order."),
        "on_load": ("When it opens", "What runs once the form is open and its record loaded."),
        "on_save": ("After saving", "What runs once the record has been saved."),
        "on_cancel": ("When it is closed unsaved", "What runs when the form is closed without saving."),
    },
    "_ScreenTabBase": {
        "id": ("Id", "A short name for this tab, unique in the form."),
        "label": ("Title", "The title of the tab."),
        "l": ("Translations", "The title in the other languages."),
        "hide_on_add": ("Hidden when adding", "Not shown while a new record is being added."),
        "hide_on_edit": ("Hidden when changing", "Not shown while an existing record is being changed."),
        "actions": ("Buttons", "The buttons shown on this tab and what each one does."),
    },
    "FormTab": {
        "type": ("Kind of tab", "A tab of fields."),
        "cols": ("Columns", "How many columns the fields are laid out on."),
        "fields": ("Fields", "The fields of the tab, in order."),
    },
})

# ── actions: a button, a row-menu entry, the steps of a service ──────────────────────────────
_STEP_TYPE: Label = ("Kind of step", "What this step does.")
_KEEP_RESULT: Label = ("Keep the result", "Keep what this step returns, so that the following steps can use it.")
_REPLAY: Label = ("Run again on transfer", "Run this call again on the other environment when a change package is applied there.")

LABELS.update({
    "_ActionBase": {
        "id": ("Id", "A short name for this step or button, unique in the screen."),
        "label": ("Label", "The text of the button or of the menu entry."),
        "l": _TRANSLATIONS,
        "stop_on_error": ("Stop on error", "When this step fails, do not run the following ones."),
    },
    "_PromptableMixin": {
        "prompt_fields": ("Questions asked first", "The fields the user fills in before this runs; none runs it at once."),
        "prompt_title": ("Title of the questions", "The title of the window that asks them; blank uses the label."),
        "prompt_l": ("Title translations", "That title in the other languages."),
        "prompt_cols": ("Columns", "How many columns the questions are laid out on."),
        "prompt_submit_label": ("Text of the button", "The text of the button that confirms; blank uses the label."),
    },
    "PromptField": {
        "name": ("Name", "The name the following steps use to read what was typed."),
        "dd": ("Dictionary item", "The dictionary item that gives the field its label, format and list."),
        "label": ("Label", "The label of the field; blank uses the dictionary's."),
        "format": ("Format", "How the value is typed: text, number, date, password…"),
        "hidden": ("Hidden", "Not shown, unless a condition below shows it."),
        "disabled": ("Read only", "Shown with its default value, which cannot be changed."),
        "required": ("Required", "The window cannot be confirmed while this field is empty."),
        "colspan": ("Width", "How many columns of the window the field takes."),
        "default": ("Default value", "The value the field starts with."),
        "lookup_param_binds": ("Values given to the list", "Other answers of the window that narrow this field's list."),
        "visible_when": ("Shown only when", "Shown only while these conditions hold on the other answers."),
        "required_when": ("Required only when", "Required only while these conditions hold on the other answers."),
        "disabled_when": ("Read only when", "Read only while these conditions hold on the other answers."),
    },
    "RunQueryAction": {
        "type": _STEP_TYPE,
        "connector": ("Application", "The application that holds the query; blank for the screen's."),
        "query": ("Query", "The query to run."),
        "param_binds": ("Values passed", "What each parameter of the query receives."),
        "bind_result": _KEEP_RESULT,
    },
    "CallApiAction": {
        "type": _STEP_TYPE,
        "connector": ("Service connector", "The connector that holds the service to call."),
        "endpoint": ("Service", "The service to call."),
        "param_binds": ("Values passed", "What each parameter of the service receives."),
        "bind_result": _KEEP_RESULT,
        "change_replay": _REPLAY,
    },
    "CallPluginAction": {
        "type": _STEP_TYPE,
        "callable": ("Function", "The function of an application to run."),
        "param_binds": ("Values passed", "What each argument of the function receives."),
        "bind_result": _KEEP_RESULT,
        "change_replay": _REPLAY,
    },
    "CallActionAction": {
        "type": _STEP_TYPE,
        "ref": ("Service", "The shared service to run."),
        "param_binds": ("Values passed", "What each input of the service receives."),
        "change_replay": _REPLAY,
    },
    "NavigateAction": {
        "type": _STEP_TYPE,
        "to": ("Query opened", "The query to open, when no screen is picked."),
        "connector": ("Application", "The application that holds what is opened; blank for this screen's."),
        "screen": ("Screen opened", "The screen to open."),
        "param_binds": ("Values handed over", "Which values of the row fill the filters of what is opened."),
        "inline": ("Open in place", "Open over this grid with a Back button instead of in a new tab."),
    },
    "OpenUrlAction": {
        "type": _STEP_TYPE,
        "url": ("Address", "The address to open; a column of the row is written in braces: {NAME}."),
        "new_tab": ("In a new tab", "Open in a new browser tab instead of in place of this page."),
    },
    "SetFieldAction": {
        "type": _STEP_TYPE,
        "target": ("Field", "The field of the form that receives the value."),
        "value": ("Fixed value", "A value written here, the same every time."),
        "source": ("Taken from", "The field or the earlier step the value is read from."),
    },
    "ConfirmAction": {
        "type": _STEP_TYPE,
        "message": ("Question", "The question asked; the following steps run only on a yes."),
        "confirm_label": ("Text for yes", "The text of the button that confirms."),
        "cancel_label": ("Text for no", "The text of the button that cancels."),
    },
    "NotifyAction": {
        "type": _STEP_TYPE,
        "message": ("Message", "The text shown to the user."),
        "tone": ("Shown as", "Information, success, warning or error."),
    },
    "SendNotificationAction": {
        "type": _STEP_TYPE,
        "rule": ("Notification rule", "The rule that says who is told and what the message is."),
        "param_binds": ("Values passed", "Values the message of the rule may show."),
    },
    "RefreshAction": {
        "type": _STEP_TYPE,
    },
    "ChainAction": {
        "type": _STEP_TYPE,
        "steps": ("Steps", "The steps, run one after the other."),
    },
    "IfAction": {
        "type": _STEP_TYPE,
        "condition": ("Condition", "What is tested."),
        "then_steps": ("If it holds", "The steps run when the condition holds."),
        "else_steps": ("Otherwise", "The steps run when it does not."),
    },
    "Condition": {
        "source": ("Value tested", "The field or the earlier step whose value is tested."),
        "operator": ("Test", "How it is compared: equal, different, greater, has rows…"),
        "value": ("Compared with", "The value it is compared with, when the test needs one."),
    },
    "LoopAction": {
        "type": _STEP_TYPE,
        "source": ("For each line of", "The rows an earlier step returned; the steps run once per row."),
        "steps": ("Steps", "The steps run for each row."),
    },
    "ReturnAction": {
        "type": _STEP_TYPE,
        "bindings": ("Fields filled", "Which field of the form receives which value of the earlier steps."),
    },
    "ScreenField": {
        "name": ("Column", "The column of the screen shown as a field."),
        "colspan": ("Width", "How many columns of the tab the field takes."),
    },
    "NestedFormTab": {
        "type": ("Kind of tab", "The form of another screen."),
        "form_screen": ("Screen", "The screen whose form is shown in this tab."),
        "connector": ("Application", "The application of that screen; blank for this one."),
        "param_binds": ("Link to the record", "Which fields of this record find the record of the other screen."),
    },
    "NestedTableTab": {
        "type": ("Kind of tab", "The rows of another screen."),
        "screen": ("Screen", "The screen whose rows are shown in this tab."),
        "connector": ("Application", "The application of that screen; blank for this one."),
        "param_binds": ("Link to the record", "Which fields of this record filter the rows of the other screen."),
    },
    "SharedActionsFile": {
        "actions": ("Services", "The services shared by the screens."),
    },
    "SharedAction": {
        "id": ("Id", "A short name for this service."),
        "label": ("Label", "The name shown where the service is picked."),
        "description": ("Description", "What this service does, in a sentence."),
        "params": ("Inputs", "The values the service receives from what calls it."),
        "prompt_fields": ("Questions asked first", "The fields the user fills in before the service runs."),
        "steps": ("Steps", "The steps, run one after the other."),
    },
    "ActionParam": {
        "name": ("Name", "The input's name, as the steps read it."),
        "label": ("Label", "The name shown where the service is called."),
        "default": ("Default value", "The value used when the caller gives none."),
        "description": ("Description", "What this input is for."),
    },
})


# ── applying the catalogue ───────────────────────────────────────────────────────────────────


def _models_of(root: type[BaseModel], found: dict[str, type[BaseModel]] | None = None) -> dict[str, type[BaseModel]]:
    """Every model reachable from *root* through its fields, by class name."""
    found = {} if found is None else found

    def walk(tp: Any) -> None:
        if get_origin(tp) is not None:
            for arg in get_args(tp):
                walk(arg)
            return
        if isinstance(tp, type) and issubclass(tp, BaseModel) and tp.__name__ not in found:
            found[tp.__name__] = tp
            for info in tp.model_fields.values():
                walk(info.annotation)

    walk(root)
    return found


def label_of(model: type[BaseModel], field: str) -> Label | None:
    """The label and hint of *field* on *model*: its own entry, else the one of the base class that
    declares the field."""
    for cls in model.__mro__:
        entry = LABELS.get(cls.__name__, {}).get(field)
        if entry is not None:
            return entry
    return None


def title_of(model: type[BaseModel], field: str) -> str | None:
    """The label of a field, for a message that names it (a refused save)."""
    entry = label_of(model, field)
    return entry[0] if entry else None


def _plain(text: str) -> str:
    return " ".join((text or "").split())


def apply_labels(schema: dict[str, Any], root: type[BaseModel]) -> dict[str, Any]:
    """Write the catalogue into the JSON schema of *root* (and of the models under ``$defs``):
    each property gets its label as ``title`` and its short sentence as ``description``; what the
    model itself says moves to ``x_more`` when it is the longer text (examples, rules, notation).
    Returns *schema*."""
    models = _models_of(root)

    def label(node: dict[str, Any], model: type[BaseModel] | None) -> None:
        if model is None:
            return
        for name, prop in (node.get("properties") or {}).items():
            entry = label_of(model, name)
            if entry is None or not isinstance(prop, dict):
                continue
            title, hint = entry
            long_text = _plain(str(prop.get("description") or ""))
            prop["title"] = title
            prop["description"] = hint
            # "more" only where the model really says more: a second sentence, an example, a rule
            if len(long_text) > max(MORE_FROM, len(hint) * 3 // 2):
                prop["x_more"] = long_text

    label(schema, root)
    for name, node in (schema.get("$defs") or {}).items():
        if isinstance(node, dict):
            label(node, models.get(name))
    return schema


_NOTATION = re.compile(r"``|`|\[\w+(\.\w+)*\]|\$\{|#[A-Z_.]+#|\bF\d{4,5}\b")


def problems_in(roots: list[type[BaseModel]]) -> list[str]:
    """What the catalogue lacks or says badly for the forms built from *roots* — one line each.
    Empty when every field has a label and one short sentence in plain words."""
    found: list[str] = []
    models: dict[str, type[BaseModel]] = {}
    for root in roots:
        _models_of(root, models)
    for cls_name, model in models.items():
        for field in model.model_fields:
            entry = label_of(model, field)
            if entry is None:
                found.append(f"{cls_name}.{field}: no entry in liberty/field_labels.py")
                continue
            title, hint = entry
            where = f"{cls_name}.{field}"
            if not title.strip() or len(title) > MAX_LABEL:
                found.append(f"{where}: the label must be 1 to {MAX_LABEL} characters ({len(title)})")
            if not hint.strip() or len(hint) > MAX_HINT:
                found.append(f"{where}: the hint must be 1 to {MAX_HINT} characters ({len(hint)})")
            if not hint.rstrip().endswith((".", "…", ")")):
                found.append(f"{where}: the hint is a sentence, it ends with a full stop")
            for text in (title, hint):
                if _NOTATION.search(text):
                    found.append(f"{where}: file notation or a code in {text!r}")
    # an entry nothing uses is a field that was renamed or removed
    for cls_name, fields in LABELS.items():
        model = models.get(cls_name)
        owners = [m for m in models.values() if any(c.__name__ == cls_name for c in m.__mro__)]
        if model is None and not owners:
            found.append(f"{cls_name}: no such model in the forms")
            continue
        for field in fields:
            if not any(field in m.model_fields for m in owners or [model]):
                found.append(f"{cls_name}.{field}: no such field")
    return found
