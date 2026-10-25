"""Streamlit interface for manual accident-news collection and review."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import streamlit as st

from database import db
from database.db import DuplicateArticleError
from services import article_service, export_service, extraction_service, screenshot_service, yearly_data_service
from services.yearly_data_service import YearlyCsvSyncError


st.set_page_config(page_title="Dehradun Accident News", page_icon="🚗", layout="wide")
db.initialize_database()


def _article_label(article: dict) -> str:
    published = article.get("published_date") or "publication date unknown"
    headline = article.get("headline") or "headline unknown"
    return f"#{article['article_id']} · {published} · {headline}"


def _sync_failure_text(error: YearlyCsvSyncError) -> str:
    result = error.operation_result
    if "saved" in result:
        return (
            f"{error} Selected year: {result.get('selected_year')}; saved: {result.get('saved')}; "
            f"duplicates skipped: {result.get('duplicates_skipped')}; "
            "the database transaction committed. Resync the affected annual CSVs to recover."
        )
    return f"{error} The database operation committed. Resync the affected annual CSVs to recover."


def _show_year_result(result: dict) -> None:
    message = (
        f"Year {result['selected_year']}: saved {result['saved']}; "
        f"duplicate records skipped {result['duplicates_skipped']}. "
        f"Occurrence-year CSVs synchronized: "
        f"{', '.join(map(str, result['affected_years'])) or 'none (accident date unknown)'}"
    )
    if result["validation_errors"] and not result["saved"] and not result["duplicates_skipped"]:
        st.warning(message)
    else:
        st.success(message)
    if result.get("unassigned_year"):
        st.warning(
            f"{result['unassigned_year']} record(s) were saved centrally with no accident year "
            "because the accident date is unknown."
        )
    for error in result["validation_errors"]:
        st.error(error)


def _render_review(article_id: int) -> None:
    article = article_service.get_article(article_id)
    if not article:
        st.error("The selected article could not be found.")
        return
    records = db.list_accident_records(article_id)
    left, right = st.columns([0.9, 1.1], gap="large")
    with left:
        st.subheader("Original article")
        st.caption(
            f"{article.get('source_name') or 'source unknown'} · "
            f"{article.get('published_date') or 'publication date unknown'} · "
            f"{article.get('language') or 'language unknown'}"
        )
        if article.get("source_url"):
            st.link_button("Open source article", article["source_url"])
        else:
            st.caption("Source URL was not present in the screenshot metadata.")
        st.text_area(
            "Complete original article text",
            value=article["article_text"],
            height=560,
            disabled=True,
            key=f"original-article-{article_id}",
        )
    with right:
        st.subheader("Extracted accident records")
        if not records:
            st.info("No road-accident records were extracted. Check the article text and language, then add details manually if appropriate.")
            return
        for record in records:
            st.markdown(f"#### Accident record #{record['accident_id']}")
            if record["possible_duplicate"]:
                st.warning("Possible duplicate incident reported by another article. Compare the source before marking it verified.")
            confidence = record["extraction_confidence"]
            confidence_label = f"{confidence:.2f}" if confidence is not None else "unknown"
            st.caption(
                f"Scope: {record['scope_status']} · Confidence: {confidence_label} · "
                f"Cause status: {record['cause_status'] or 'unknown'}"
            )
            with st.form(f"review-record-{record['accident_id']}"):
                c1, c2 = st.columns(2)
                with c1:
                    accident_date = st.text_input("Incident date (YYYY-MM-DD)", value=record["accident_date"] or "", key=f"date-{record['accident_id']}")
                    accident_time = st.text_input("Incident time (HH:MM)", value=record["accident_time"] or "", key=f"time-{record['accident_id']}")
                    district = st.text_input("District", value=record["district"] or "", key=f"district-{record['accident_id']}")
                    area = st.text_input("Area / locality", value=record["area"] or "", key=f"area-{record['accident_id']}")
                    accident_type = st.text_input("Accident type", value=record["accident_type"] or "", key=f"type-{record['accident_id']}")
                with c2:
                    cause = st.text_input("Cause (reported/suspected)", value=record["cause"] or "", key=f"cause-{record['accident_id']}")
                    vehicles = st.text_input("Vehicles involved", value=record["vehicles_involved"] or "", key=f"vehicles-{record['accident_id']}")
                    deaths = st.text_input("Deaths (blank means unknown)", value="" if record["deaths"] is None else str(record["deaths"]), key=f"deaths-{record['accident_id']}")
                    injuries = st.text_input("Injuries (blank means unknown)", value="" if record["injuries"] is None else str(record["injuries"]), key=f"injuries-{record['accident_id']}")
                    weather = st.text_input("Weather / road conditions", value=record["weather"] or "", key=f"weather-{record['accident_id']}")
                statuses = ["pending", "needs_review", "verified"]
                status = st.selectbox(
                    "Review status (mark verified only after checking the article)",
                    statuses,
                    index=statuses.index(record["review_status"]),
                    key=f"status-{record['accident_id']}",
                )
                submitted = st.form_submit_button("Save corrections")
            if submitted:
                try:
                    extraction_service.save_review(
                        record["accident_id"],
                        {
                            "accident_date": accident_date,
                            "accident_time": accident_time,
                            "district": district,
                            "area": area,
                            "accident_type": accident_type,
                            "cause": cause,
                            "vehicles_involved": vehicles,
                            "deaths": deaths,
                            "injuries": injuries,
                            "weather": weather,
                        },
                        review_status=status,
                    )
                    st.success("Corrections saved. Field provenance is recorded in the database.")
                    st.rerun()
                except ValueError as error:
                    st.error(str(error))
                except YearlyCsvSyncError as error:
                    st.error(_sync_failure_text(error))
                except Exception as error:
                    st.error(f"Database operation failed; no success was reported: {error}")


st.title("Dehradun Road Accident News")
st.caption("Collect English or Hindi reports for any year, review extracted facts, and maintain synchronized exports.")
page = st.sidebar.radio(
    "Workflow",
    ["Collect article", "Upload News Screenshot", "Articles", "Review records", "Yearly data", "CSV export"],
)

if page == "Collect article":
    st.header("Add a news article")
    with st.form("article-entry"):
        headline = st.text_input("News headline *")
        article_text = st.text_area("Full article text *", height=420, max_chars=None, help="Paste the complete article. The saved source text is kept unchanged.")
        col1, col2 = st.columns(2)
        with col1:
            source_name = st.text_input("Source name *")
            source_url = st.text_input("Original article URL *")
        with col2:
            published_date = st.date_input("Publication date *", value=date.today())
            language = st.selectbox("Language *", ["English", "Hindi"])
        submitted = st.form_submit_button("Save Article")
    if submitted:
        try:
            article_id = article_service.save_article(
                {
                    "headline": headline,
                    "article_text": article_text,
                    "source_name": source_name,
                    "source_url": source_url,
                    "published_date": published_date,
                    "language": language,
                }
            )
            st.session_state["last_saved_article_id"] = article_id
            records = extraction_service.extract_and_save_article(article_id)
            st.success(f"Article #{article_id} saved. Extracted {len(records)} accident record(s), all awaiting human review.")
        except YearlyCsvSyncError as error:
            st.error(_sync_failure_text(error))
        except DuplicateArticleError as error:
            st.error(f"{error} Open the existing article from the Articles page.")
        except ValueError as error:
            st.error(str(error))
        except Exception as error:
            st.error(f"Article save or extraction failed: {error}")
    article_id = st.session_state.get("last_saved_article_id")
    if article_id:
        st.divider()
        _render_review(article_id)

elif page == "Upload News Screenshot":
    st.header("Upload News Screenshot")
    st.caption(
        "Upload one or more screenshots from the same article. Originals and page-level OCR are retained; "
        "only an approved incident is added to the verified dataset."
    )
    uploads = st.file_uploader(
        "Article screenshot(s)",
        type=["jpg", "jpeg", "png"],
        accept_multiple_files=True,
        help="Choose several pages from one article together. Each original image is preserved for audit.",
        key="screenshot-upload-files",
    )
    with st.form("screenshot-upload-form"):
        meta_left, meta_right = st.columns(2)
        with meta_left:
            screenshot_headline = st.text_input("Headline (leave blank if unknown)")
            screenshot_source = st.text_input("Source name (leave blank if unknown)")
            screenshot_url = st.text_input("Source URL (optional)")
        with meta_right:
            screenshot_published = st.text_input("Publication date (YYYY-MM-DD; blank if unknown)")
            screenshot_language = st.selectbox("Article language", ["Unknown", "English", "Hindi"])
        create_screenshot = st.form_submit_button("Create draft and run OCR")
    if create_screenshot:
        try:
            screenshot_id = screenshot_service.create_draft(
                [(upload.name, upload.getvalue()) for upload in (uploads or [])],
                {
                    "headline": screenshot_headline,
                    "source_name": screenshot_source,
                    "source_url": screenshot_url,
                    "published_date": screenshot_published,
                    "language": None if screenshot_language == "Unknown" else screenshot_language,
                },
            )
            st.session_state["active_screenshot_article_id"] = screenshot_id
            st.success(f"Draft {screenshot_id} created. Check the OCR and correct the article text below.")
            st.rerun()
        except screenshot_service.DuplicateScreenshotError as error:
            st.error(f"{error} Open that existing screenshot article to review it.")
        except (ValueError, OSError) as error:
            st.error(str(error))
        except Exception as error:
            st.error(f"Screenshot draft creation failed: {error}")

    screenshot_drafts = screenshot_service.list_drafts()
    if not screenshot_drafts:
        st.info("No screenshot drafts have been created yet.")
    else:
        draft_ids = [item["screenshot_article_id"] for item in screenshot_drafts]
        labels = {
            item["screenshot_article_id"]: (
                f"{item.get('headline') or 'Untitled article'} · {item['page_count']} page(s) · "
                f"{item['status']} · {item['screenshot_article_id'][:8]}"
            )
            for item in screenshot_drafts
        }
        active_id = st.session_state.get("active_screenshot_article_id")
        selected_index = draft_ids.index(active_id) if active_id in draft_ids else 0
        selected_screenshot_id = st.selectbox(
            "Open a screenshot article draft",
            draft_ids,
            index=selected_index,
            format_func=lambda value: labels[value],
        )
        screenshot_draft = screenshot_service.get_draft(selected_screenshot_id)
        if screenshot_draft:
            st.caption(
                f"Article ID: {screenshot_draft['screenshot_article_id']} · "
                f"Status: {screenshot_draft['status']} · "
                f"Created: {screenshot_draft['created_at']}"
            )
            pages = screenshot_draft["pages"]
            if pages:
                page_labels = [f"Page {page['page_order']}: {page['original_filename']}" for page in pages]
                page_choice = st.selectbox(
                    "Screenshot to inspect", list(range(len(pages))),
                    format_func=lambda index: page_labels[index],
                    key=f"screenshot-page-{selected_screenshot_id}",
                )
                selected_page = pages[page_choice]
            else:
                selected_page = None

            article_left, article_right = st.columns([0.9, 1.1], gap="large")
            with article_left:
                st.subheader("Original screenshot")
                if selected_page:
                    image_path = Path(selected_page["image_path"])
                    if image_path.is_file():
                        st.image(str(image_path), width="stretch")
                    else:
                        st.warning(f"Original image file is missing: {image_path}")
                    status = selected_page["readability_status"]
                    if status == "unreadable":
                        st.error("OCR could not read this image. Transcribe it manually in the article text field.")
                    elif status == "uncertain":
                        st.warning("OCR is uncertain on this page. Check names, dates, locations, and counts against the image.")
                    else:
                        st.success(f"OCR confidence: {selected_page['ocr_confidence'] or 'unknown'}%")
                    if selected_page.get("ocr_error"):
                        st.caption(f"OCR note: {selected_page['ocr_error']}")
                    with st.expander("Raw OCR output for this page"):
                        st.text_area(
                            "Raw OCR output (preserved)", value=selected_page["raw_ocr_text"],
                            height=240, disabled=True,
                            key=f"screenshot-raw-page-{selected_page['screenshot_page_id']}",
                        )
                else:
                    st.info("No screenshot page is attached.")

            with article_right:
                st.subheader("Review article text")
                metadata_locked = screenshot_draft.get("central_article_id") is not None
                if metadata_locked:
                    st.info("Article metadata and text are locked because at least one incident has been approved.")
                with st.form(f"screenshot-edit-{selected_screenshot_id}"):
                    edited_headline = st.text_input(
                        "Headline (blank if unknown)", value=screenshot_draft.get("headline") or "",
                        disabled=metadata_locked,
                    )
                    edited_source = st.text_input(
                        "Source name", value=screenshot_draft.get("source_name") or "",
                        disabled=metadata_locked,
                    )
                    edited_url = st.text_input(
                        "Source URL", value=screenshot_draft.get("source_url") or "",
                        disabled=metadata_locked,
                    )
                    edited_published = st.text_input(
                        "Publication date (YYYY-MM-DD; blank if unknown)",
                        value=screenshot_draft.get("published_date") or "",
                        disabled=metadata_locked,
                    )
                    language_values = ["Unknown", "English", "Hindi"]
                    current_language = screenshot_draft.get("language") or "Unknown"
                    edited_language = st.selectbox(
                        "Article language", language_values,
                        index=language_values.index(current_language) if current_language in language_values else 0,
                        disabled=metadata_locked,
                    )
                    st.text_area(
                        "Raw OCR for all pages (audit copy)",
                        value=screenshot_draft["raw_ocr_text"], height=150, disabled=True,
                        key=f"screenshot-raw-all-{selected_screenshot_id}",
                    )
                    edited_article_text = st.text_area(
                        "Corrected article text", value=screenshot_draft["reviewed_article_text"],
                        height=320, disabled=metadata_locked,
                        help="This editable text is separate from the preserved raw OCR output.",
                    )
                    save_text = st.form_submit_button("Save text and metadata", disabled=metadata_locked)
                if save_text:
                    try:
                        screenshot_service.save_draft(
                            selected_screenshot_id,
                            {
                                "headline": edited_headline,
                                "source_name": edited_source,
                                "source_url": edited_url,
                                "published_date": edited_published,
                                "language": None if edited_language == "Unknown" else edited_language,
                                "reviewed_article_text": edited_article_text,
                            },
                        )
                        st.success("Corrected article text and metadata saved as a draft.")
                        st.rerun()
                    except (ValueError, OSError) as error:
                        st.error(str(error))

            if not metadata_locked:
                if st.button("Extract accident details from saved text", key=f"extract-screenshot-{selected_screenshot_id}"):
                    try:
                        records = screenshot_service.extract_draft_records(selected_screenshot_id)
                        if records:
                            st.success(f"{len(records)} incident draft(s) ready for review.")
                        else:
                            st.warning("No road-accident details were extracted. The article remains saved as a draft.")
                        st.rerun()
                    except (ValueError, OSError) as error:
                        st.error(str(error))
                    except Exception as error:
                        st.error(f"Extraction failed: {error}")

            if screenshot_draft["records"]:
                st.subheader("Extracted incident drafts")
                st.caption("Publication date and accident date are separate. Unknown accident dates stay unassigned to a year.")
                for screenshot_record in screenshot_draft["records"]:
                    record_id = screenshot_record["screenshot_record_id"]
                    record = screenshot_record["record"]
                    status = screenshot_record["status"]
                    with st.expander(
                        f"Incident {screenshot_record['record_number']} · {status} · "
                        f"{record.get('headline') or 'headline unknown'}",
                        expanded=status != "approved",
                    ):
                        if screenshot_record["conflicts"]:
                            st.warning("Review conflicts: " + " ".join(screenshot_record["conflicts"]))
                        if screenshot_record["evidence"]:
                            with st.expander("Supporting text evidence"):
                                st.json(screenshot_record["evidence"])
                        if status == "approved":
                            if screenshot_record.get("central_accident_id") is not None:
                                st.success(
                                    f"Verified record #{screenshot_record['central_accident_id']} is in the central database."
                                )
                            else:
                                st.warning("This incident was approved earlier, then removed from the central dataset. Screenshot audit data is retained.")
                            continue
                        record_image, record_fields = st.columns([0.8, 1.2], gap="large")
                        with record_image:
                            if selected_page and Path(selected_page["image_path"]).is_file():
                                st.image(str(selected_page["image_path"]), width="stretch")
                        with record_fields:
                            with st.form(f"screenshot-record-{record_id}"):
                                field_left, field_right = st.columns(2)
                                with field_left:
                                    record_headline = st.text_input("Headline", value=record.get("headline") or "", key=f"sr-headline-{record_id}")
                                    record_published = st.text_input("Publication date (YYYY-MM-DD)", value=record.get("published_date") or "", key=f"sr-published-{record_id}")
                                    record_date = st.text_input("Accident date (YYYY-MM-DD; blank if unknown)", value=record.get("accident_date") or "", key=f"sr-date-{record_id}")
                                    record_time = st.text_input("Accident time (HH:MM; blank if unknown)", value=record.get("accident_time") or "", key=f"sr-time-{record_id}")
                                    record_district = st.text_input("District", value=record.get("district") or "", key=f"sr-district-{record_id}")
                                    record_area = st.text_input("Locality / exact area", value=record.get("area") or "", key=f"sr-area-{record_id}")
                                    record_type = st.text_input("Accident type", value=record.get("accident_type") or "", key=f"sr-type-{record_id}")
                                with field_right:
                                    record_source = st.text_input("Source name", value=record.get("source_name") or "", key=f"sr-source-{record_id}")
                                    record_url = st.text_input("Source URL", value=record.get("source_url") or "", key=f"sr-url-{record_id}")
                                    record_cause = st.text_input("Cause", value=record.get("cause") or "", key=f"sr-cause-{record_id}")
                                    record_weather = st.text_input("Weather / road conditions", value=record.get("weather") or "", key=f"sr-weather-{record_id}")
                                    record_vehicles = st.text_input("Vehicles involved", value=record.get("vehicles_involved") or "", key=f"sr-vehicles-{record_id}")
                                    record_deaths = st.text_input("Deaths (blank if unknown)", value="" if record.get("deaths") is None else str(record["deaths"]), key=f"sr-deaths-{record_id}")
                                    record_injuries = st.text_input("Injuries (blank if unknown)", value="" if record.get("injuries") is None else str(record["injuries"]), key=f"sr-injuries-{record_id}")
                                    record_language_value = record.get("language") or "Unknown"
                                    record_language = st.selectbox(
                                        "Language", ["Unknown", "English", "Hindi"],
                                        index=["Unknown", "English", "Hindi"].index(record_language_value)
                                        if record_language_value in {"Unknown", "English", "Hindi"} else 0,
                                        key=f"sr-language-{record_id}",
                                    )
                                save_record_draft = st.form_submit_button("Save as draft")
                                approve_record = st.form_submit_button("Approve and add to verified dataset")
                                reject_record = st.form_submit_button("Reject this incident")
                            record_updates = {
                                "headline": record_headline,
                                "published_date": record_published,
                                "accident_date": record_date,
                                "accident_time": record_time,
                                "district": record_district,
                                "area": record_area,
                                "accident_type": record_type,
                                "source_name": record_source,
                                "source_url": record_url,
                                "cause": record_cause,
                                "weather": record_weather,
                                "vehicles_involved": record_vehicles,
                                "deaths": record_deaths,
                                "injuries": record_injuries,
                                "language": None if record_language == "Unknown" else record_language,
                            }
                            try:
                                if save_record_draft:
                                    screenshot_service.save_draft_record(record_id, record_updates)
                                    st.success("Incident draft saved.")
                                    st.rerun()
                                elif approve_record:
                                    screenshot_service.save_draft_record(record_id, record_updates)
                                    approval = screenshot_service.approve_draft_record(record_id)
                                    st.success(f"Verified record #{approval['accident_id']} approved and saved.")
                                    if approval.get("unassigned_year"):
                                        st.info("Accident date is unknown, so this record remains unassigned to a year.")
                                    st.rerun()
                                elif reject_record:
                                    screenshot_service.save_draft_record(record_id, record_updates, status="rejected")
                                    st.warning("Incident rejected; no verified accident record was created.")
                                    st.rerun()
                            except YearlyCsvSyncError as error:
                                st.error(_sync_failure_text(error))
                            except (ValueError, OSError) as error:
                                st.error(str(error))
                            except Exception as error:
                                st.error(f"Incident review failed: {error}")

            if screenshot_draft.get("central_article_id") is None and screenshot_draft["status"] != "rejected":
                if st.button("Reject entire article draft", key=f"reject-screenshot-{selected_screenshot_id}"):
                    try:
                        screenshot_service.reject_draft_article(selected_screenshot_id)
                        st.success("Article draft rejected and retained for audit.")
                        st.rerun()
                    except ValueError as error:
                        st.error(str(error))

elif page == "Articles":
    st.header("Collected articles")
    articles = article_service.list_articles()
    if not articles:
        st.info("No articles have been saved yet.")
    else:
        st.dataframe(articles, hide_index=True, use_container_width=True)
        article_ids = [row["article_id"] for row in articles]
        selected_id = st.selectbox(
            "Open an article",
            article_ids,
            format_func=lambda value: _article_label(next(row for row in articles if row["article_id"] == value)),
        )
        article = article_service.get_article(selected_id)
        st.subheader(article["headline"])
        st.caption(
            f"{article.get('source_name') or 'source unknown'} · "
            f"{article.get('published_date') or 'publication date unknown'} · "
            f"{article.get('language') or 'language unknown'}"
        )
        if article.get("source_url"):
            st.link_button("Open source article", article["source_url"])
        else:
            st.caption("No source URL was recorded for this screenshot article.")
        st.text_area("Complete original article text", value=article["article_text"], height=520, disabled=True)

elif page == "Review records":
    st.header("Review and correct extractions")
    articles = article_service.list_articles()
    if not articles:
        st.info("Save an article first.")
    else:
        article_ids = [row["article_id"] for row in articles]
        default_id = st.session_state.get("last_saved_article_id")
        index = article_ids.index(default_id) if default_id in article_ids else 0
        selected_id = st.selectbox(
            "Article",
            article_ids,
            index=index,
            format_func=lambda value: _article_label(next(row for row in articles if row["article_id"] == value)),
        )
        if not db.list_accident_records(selected_id):
            if st.button("Run extraction"):
                records = extraction_service.extract_and_save_article(selected_id)
                st.success(f"Created {len(records)} accident record(s).")
                st.rerun()
        _render_review(selected_id)

elif page == "Yearly data":
    st.header("Year-based accident records")
    st.caption("Annual CSV files are grouped by accident occurrence year, derived from accident_date. The table filter can use either accident year or publication year.")
    basis_label = st.selectbox("Filter records by", ["Accident occurrence year", "Publication year"])
    year_basis = "accident" if basis_label == "Accident occurrence year" else "publication"
    available_years = yearly_data_service.available_years(year_basis=year_basis)
    year_options = ["Enter a year"] + [str(year) for year in available_years]
    year_choice = st.selectbox("Select an existing year or enter a new one", year_options)
    if year_choice == "Enter a year":
        year_input = st.text_input(
            "New or unlisted year (YYYY)", value=str(date.today().year),
            help="Any four-digit historical or future year from 0001 through 9999 is accepted.",
        )
        try:
            selected_year = yearly_data_service.validate_year(year_input)
        except ValueError as error:
            st.error(str(error))
            st.stop()
    else:
        selected_year = int(year_choice)
    st.info(f"Current filter: {basis_label.lower()} = {selected_year}. Records without that date are not included in this view.")

    with st.expander("Add a structured accident record", expanded=False):
        with st.form("structured-record-entry"):
            headline = st.text_input("Headline *")
            left, right = st.columns(2)
            with left:
                accident_date = st.text_input("Accident date (YYYY-MM-DD; blank if unknown)")
                published_date = st.text_input("Publication date (YYYY-MM-DD; blank if unknown)")
                district = st.text_input("District")
                area = st.text_input("Area / locality")
                accident_type = st.text_input("Accident type")
                cause = st.text_input("Reported or suspected cause")
            with right:
                accident_time = st.text_input("Accident time (HH:MM; blank if unknown)")
                vehicles = st.text_input("Vehicles involved")
                deaths = st.text_input("Deaths (blank means unknown)")
                injuries = st.text_input("Injuries (blank means unknown)")
                weather = st.text_input("Weather / road conditions")
                source_name = st.text_input("Source name")
                source_url = st.text_input("Source URL")
                language = st.selectbox("Language", ["", "English", "Hindi"])
                articles = article_service.list_articles()
                article_options = [0] + [item["article_id"] for item in articles]
                linked_article_id = st.selectbox(
                    "Link a previously saved source article (optional)",
                    article_options,
                    format_func=lambda value: "No linked article" if value == 0 else _article_label(next(row for row in articles if row["article_id"] == value)),
                )
            add_submitted = st.form_submit_button("Save accident record")
        if add_submitted:
            try:
                result = yearly_data_service.add_record(
                    {
                        "headline": headline,
                        "accident_date": accident_date,
                        "published_date": published_date,
                        "district": district,
                        "area": area,
                        "accident_type": accident_type,
                        "cause": cause,
                        "accident_time": accident_time,
                        "vehicles_involved": vehicles,
                        "deaths": deaths,
                        "injuries": injuries,
                        "weather": weather,
                        "source_name": source_name,
                        "source_url": source_url,
                        "language": language,
                        "article_id": linked_article_id or None,
                    },
                    selected_year=selected_year,
                    year_basis=year_basis,
                )
                _show_year_result(result)
            except YearlyCsvSyncError as error:
                st.error(_sync_failure_text(error))
            except (ValueError, OSError) as error:
                st.error(str(error))
            except Exception as error:
                st.error(f"Database operation failed; no success was reported: {error}")

    upload = st.file_uploader("Import structured records from CSV", type=["csv"])
    if upload is not None and st.button("Import CSV records"):
        try:
            result = yearly_data_service.import_csv(
                upload, selected_year=selected_year, year_basis=year_basis
            )
            _show_year_result(result)
        except YearlyCsvSyncError as error:
            st.error(_sync_failure_text(error))
        except (ValueError, OSError) as error:
            st.error(str(error))
        except Exception as error:
            st.error(f"Database operation failed; no success was reported: {error}")

    try:
        records = yearly_data_service.list_records(selected_year, year_basis=year_basis)
    except ValueError as error:
        records = []
        st.error(str(error))
    st.subheader(f"{len(records)} record(s) · {basis_label} {selected_year}")
    if not records:
        st.info("No records match this filter. Saving the first record with a known accident date creates the corresponding annual CSV.")
    else:
        hidden = {"article_text", "extracted_fields_json", "field_sources_json", "conflict_notes_json"}
        st.dataframe(
            [{key: value for key, value in record.items() if key not in hidden} for record in records],
            hide_index=True,
            use_container_width=True,
        )
        filtered_csv = export_service.export_dataframe(year=selected_year, year_basis=year_basis)
        st.download_button(
            "Download filtered records CSV",
            data=filtered_csv.to_csv(index=False, encoding="utf-8-sig", na_rep="").encode("utf-8-sig"),
            file_name=f"accidents_{selected_year:04d}_{year_basis}.csv",
            mime="text/csv",
        )

    if year_basis == "accident":
        if st.button("Resync yearly CSV from database"):
            try:
                output = yearly_data_service.sync_year(selected_year)
                st.success(f"Year {selected_year} CSV synchronized: {output}")
            except (ValueError, OSError, YearlyCsvSyncError) as error:
                st.error(f"Yearly CSV synchronization failed: {error}")
        annual_path = yearly_data_service.yearly_csv_path(selected_year)
        if annual_path.exists():
            st.download_button(
                f"Download accidents_{selected_year:04d}.csv",
                data=annual_path.read_bytes(),
                file_name=annual_path.name,
                mime="text/csv",
            )

        if st.button("Resync all yearly CSVs from database"):
            try:
                occurrence_years = yearly_data_service.available_years(year_basis="accident")
                paths = yearly_data_service.sync_years(occurrence_years)
                st.success(f"Synchronized {len(paths)} yearly CSV file(s).")
            except YearlyCsvSyncError as error:
                st.error(f"Some annual CSVs could not be synchronized: {error}")

    if st.button("Generate all-years CSV"):
        try:
            all_years_path = yearly_data_service.export_all_years()
            st.session_state["all_years_export_path"] = str(all_years_path)
            st.success(f"All-years export written: {all_years_path}")
        except OSError as error:
            st.error(f"All-years export failed: {error}")
    all_years_export_path = st.session_state.get("all_years_export_path")
    if all_years_export_path and Path(all_years_export_path).exists():
        st.download_button(
            "Download all_years.csv",
            data=Path(all_years_export_path).read_bytes(),
            file_name="all_years.csv",
            mime="text/csv",
        )

    if records:
        st.subheader("Update or delete a record")
        for record in records:
            with st.expander(f"Record #{record['accident_id']} · {record['headline']}"):
                with st.form(f"yearly-edit-{record['accident_id']}"):
                    edit_left, edit_right = st.columns(2)
                    with edit_left:
                        headline_edit = st.text_input("Headline", value=record["headline"] or "", key=f"headline-edit-{record['accident_id']}")
                        accident_date_edit = st.text_input("Accident date", value=record["accident_date"] or "", key=f"date-edit-{record['accident_id']}")
                        published_edit = st.text_input("Publication date", value=record["published_date"] or "", key=f"published-edit-{record['accident_id']}")
                        district_edit = st.text_input("District", value=record["district"] or "", key=f"district-edit-{record['accident_id']}")
                        area_edit = st.text_input("Area", value=record["area"] or "", key=f"area-edit-{record['accident_id']}")
                        type_edit = st.text_input("Accident type", value=record["accident_type"] or "", key=f"type-edit-{record['accident_id']}")
                        cause_edit = st.text_input("Cause", value=record["cause"] or "", key=f"cause-edit-{record['accident_id']}")
                    with edit_right:
                        time_edit = st.text_input("Accident time (HH:MM)", value=record["accident_time"] or "", key=f"time-edit-{record['accident_id']}")
                        vehicles_edit = st.text_input("Vehicles involved", value=record["vehicles_involved"] or "", key=f"vehicles-edit-{record['accident_id']}")
                        deaths_edit = st.text_input("Deaths", value="" if record["deaths"] is None else str(record["deaths"]), key=f"deaths-edit-{record['accident_id']}")
                        injuries_edit = st.text_input("Injuries", value="" if record["injuries"] is None else str(record["injuries"]), key=f"injuries-edit-{record['accident_id']}")
                        weather_edit = st.text_input("Weather / road conditions", value=record["weather"] or "", key=f"weather-edit-{record['accident_id']}")
                        source_name_edit = st.text_input("Source name", value=record["source_name"] or "", key=f"source-edit-{record['accident_id']}")
                        source_url_edit = st.text_input("Source URL", value=record["source_url"] or "", key=f"url-edit-{record['accident_id']}")
                        language_options = ["", "English", "Hindi"]
                        language_current = record["language"] or ""
                        language_edit = st.selectbox("Language", language_options, index=language_options.index(language_current) if language_current in language_options else 0, key=f"language-edit-{record['accident_id']}")
                    review_status_edit = st.selectbox("Review status", ["pending", "needs_review", "verified"], index=["pending", "needs_review", "verified"].index(record["review_status"]), key=f"review-edit-{record['accident_id']}")
                    save_edit = st.form_submit_button("Save record updates")
                if save_edit:
                    try:
                        updated = yearly_data_service.update_record(
                            record["accident_id"],
                            {
                                "headline": headline_edit,
                                "accident_date": accident_date_edit,
                                "published_date": published_edit,
                                "district": district_edit,
                                "area": area_edit,
                                "accident_type": type_edit,
                                "cause": cause_edit,
                                "accident_time": time_edit,
                                "vehicles_involved": vehicles_edit,
                                "deaths": deaths_edit,
                                "injuries": injuries_edit,
                                "weather": weather_edit,
                                "source_name": source_name_edit,
                                "source_url": source_url_edit,
                                "language": language_edit,
                                "review_status": review_status_edit,
                            },
                        )
                        st.success(f"Record updated. Synchronized CSV year(s): {updated['old_year']} → {updated['new_year']}.")
                        st.rerun()
                    except YearlyCsvSyncError as error:
                        st.error(_sync_failure_text(error))
                    except (ValueError, OSError) as error:
                        st.error(str(error))
                    except Exception as error:
                        st.error(f"Database operation failed; no success was reported: {error}")
                confirm_delete = st.checkbox("Confirm deletion of this record", key=f"confirm-delete-{record['accident_id']}")
                if st.button("Delete record", key=f"delete-record-{record['accident_id']}", disabled=not confirm_delete):
                    try:
                        deleted = yearly_data_service.delete_record(record["accident_id"])
                        st.success(f"Record deleted; year {deleted['deleted_year']} CSV synchronized.")
                        st.rerun()
                    except YearlyCsvSyncError as error:
                        st.error(_sync_failure_text(error))
                    except (ValueError, OSError) as error:
                        st.error(str(error))
                    except Exception as error:
                        st.error(f"Database operation failed; no success was reported: {error}")

elif page == "CSV export":
    st.header("Export structured accident records")
    export_choice = st.radio("Records to export", ["All records", "Verified records only"])
    verified_only = export_choice == "Verified records only"
    if st.button("Generate accident_records.csv"):
        output = export_service.export_csv(verified_only=verified_only)
        st.session_state["last_export_path"] = str(output)
        st.success(f"Wrote {output}")
    output_path = st.session_state.get("last_export_path")
    if output_path:
        try:
            with open(output_path, "rb") as export_file:
                data = export_file.read()
            st.download_button("Download accident_records.csv", data=data, file_name="accident_records.csv", mime="text/csv")
        except OSError as error:
            st.error(f"Could not read the generated CSV: {error}")
