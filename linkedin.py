import json
import os
import random
import re
import time
import traceback

from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    NoSuchShadowRootException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager
from webdriver_manager.firefox import GeckoDriverManager

import config
import constants
import utils
from linkedin_easy_apply.history import record_outcome
from linkedin_easy_apply.job_card import (
    SearchCard,
    apply_card_fallbacks,
    card_skip_decision,
    normalize_job_id,
    parse_search_card,
    should_skip_job,
    should_skip_workplace,
)
from linkedin_easy_apply.job_fit import snippet_is_usable
from linkedin_easy_apply.modal_detection import (
    ACTION_BUTTON_SELECTOR,
    DEEP_APPLICATION_ACTION_JS,
    DEEP_APPLICATION_CONTAINER_JS,
    DIALOG_SELECTORS,
    FIELD_SELECTORS,
    INTEROP_HOST_SELECTOR,
    PROGRESS_SELECTOR,
    classify_application_action,
    is_application_container_candidate,
)
from linkedin_easy_apply.pacing import should_take_long_break, sleep_human
from linkedin_easy_apply.quota import record_application, remaining_applications
from linkedin_easy_apply.operator_settings import resolved_resume_path
from utils import prGreen, prRed, prYellow


class Linkedin:
    modalSelector = ", ".join(DIALOG_SELECTORS)

    def __init__(self, store=None, run_id=None):
        browser = config.browser[0].lower()
        linkedinEmail = config.email
        if (browser == "firefox"):
            self.driver = webdriver.Firefox(
                options=utils.browserOptions(),
                service=Service(executable_path=GeckoDriverManager().install())
            )
        elif (browser == "chrome"):
            self.driver = webdriver.Chrome(
                service=ChromeService(ChromeDriverManager().install())
            )
        else:
            raise ValueError("config.browser must be either Firefox or Chrome")

        self.wait = WebDriverWait(self.driver, 30)
        self.driver.set_window_size(1440, 1000)
        self.store = store
        self.run_id = run_id
        self.last_failure_artifacts: tuple[str, str] = ("", "")
        self.last_modal_detection: dict[str, bool] = {}
        self.current_job_details: dict = {}
        self._pending_fit_score: float | None = None
        self.last_fit: dict | None = None
        self._card_fit_passed: set[str] = set()
        self.login_if_needed(linkedinEmail, config.password)

    def observe(self, message: str, job_id: object | None = None, level: str = "info") -> None:
        """Record non-sensitive state transitions for operators."""
        prYellow(message)
        store = getattr(self, "store", None)
        run_id = getattr(self, "run_id", None)
        if store and run_id:
            store.add_event(
                run_id,
                level,
                str(job_id) if job_id is not None else None,
                message,
            )

    def record_job(self, url: str, status: str, reason: str, details: dict | None = None,
                   screenshot_path: str = "", html_path: str = "",
                   fit_score: float | None = None) -> None:
        info = details or {}
        score = fit_score
        if score is None:
            score = info.get("fit_score")
        if score is None:
            score = getattr(self, "_pending_fit_score", None)
        record_outcome(
            self.store,
            self.run_id,
            url=url,
            title=str(info.get("title") or ""),
            company=str(info.get("company") or ""),
            location=str(info.get("location") or info.get("location_text") or ""),
            location_text=str(info.get("location_text") or info.get("location") or ""),
            workplace=str(info.get("workplace") or ""),
            status=status,
            reason=reason,
            screenshot_path=screenshot_path,
            html_path=html_path,
            description=str(info.get("description") or info.get("snippet") or ""),
            sector=str(info.get("sector") or ""),
            market=str(info.get("market") or ""),
            already_open=str(info.get("page_open", True)).strip().lower()
            not in {"", "0", "false", "no"},
            fit_score=score,
        )

    @staticmethod
    def pace() -> str:
        return getattr(config, "pace", "human")

    def login_if_needed(self, email, password):
        self.driver.get("https://www.linkedin.com/feed/")
        if "/login" not in self.driver.current_url:
            return
        if not email or not password:
            raise RuntimeError(
                "LinkedIn is not logged in. Log into the configured Firefox profile, "
                "or set LINKEDIN_EMAIL and LINKEDIN_PASSWORD."
            )
        prYellow("Trying to log in to LinkedIn.")
        self.wait.until(EC.visibility_of_element_located((By.ID, "username"))).send_keys(email)
        self.driver.find_element(By.ID, "password").send_keys(password)
        self.driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
        try:
            self.wait.until(lambda driver: "/login" not in driver.current_url)
        except TimeoutException as exc:
            raise RuntimeError(
                "LinkedIn did not complete login. Finish any CAPTCHA, 2FA, or security check manually."
            ) from exc

        # driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()))
        # webdriver.Chrome(ChromeDriverManager().install())
        # webdriver.Firefox(options=utils.browserOptions())
    
    def generateUrls(self):
        if not os.path.exists('data'):
            os.makedirs('data')
        try: 
            with open('data/urlData.txt', 'w',encoding="utf-8" ) as file:
                linkedinJobLinks = utils.LinkedinUrlGenerate().generateUrlLinks()
                file.writelines(url+ "\n" for url in linkedinJobLinks)
            prGreen("Urls are created successfully, now the bot will visit those urls.")
        except (OSError, TypeError, ValueError):
            prRed("Couldnt generate url, make sure you have /data folder and modified config.py file for your preferances.")

    def linkJobApply(self):
        self.generateUrls()
        countApplied = 0
        countJobs = 0
        run_cap = int(getattr(config, "max_applications_per_run", 12))
        day_cap = int(getattr(config, "max_applications_per_day", 25))
        if remaining_applications(countApplied, run_cap, day_cap, store=self.store) <= 0:
            prYellow("Stopping: the daily or per-run application cap is already reached.")
            return

        urlData = utils.getUrlDataFile()

        for url in urlData:
            if remaining_applications(countApplied, run_cap, day_cap, store=self.store) <= 0:
                prYellow("Reached the application cap. Stopping to protect the account.")
                return
            self.driver.get(url)
            sleep_human(self.pace(), "search_page")

            try:
                totalJobs = self.wait.until(EC.presence_of_element_located((
                    By.CSS_SELECTOR,
                    ".jobs-search-results-list__subtitle, .jobs-search-results-list__text"
                ))).text
                totalPages = utils.jobsToPages(totalJobs)
            except (TimeoutException, ValueError):
                totalJobs = "unknown"
                totalPages = 1

            urlWords =  utils.urlToKeywords(url)
            lineToWrite = "\n Category: " + urlWords[0] + ", Location: " +urlWords[1] + ", Applying " +str(totalJobs)+ " jobs."
            self.displayWriteResults(lineToWrite)
            if self.store and self.run_id:
                self.store.add_event(
                    self.run_id,
                    "info",
                    None,
                    lineToWrite.strip(),
                    payload_json=json.dumps(
                        {
                            "kind": "search",
                            "keyword": urlWords[0],
                            "location": urlWords[1],
                        }
                    ),
                )

            for page in range(totalPages):
                if remaining_applications(countApplied, run_cap, day_cap, store=self.store) <= 0:
                    prYellow("Reached the application cap. Stopping to protect the account.")
                    return
                currentPageJobs = constants.jobsPerPage * page
                pageUrl = url.rstrip() + "&start=" + str(currentPageJobs)
                self.driver.get(pageUrl)
                sleep_human(self.pace(), "search_page")

                offersPerPage = self.driver.find_elements(By.XPATH,'//li[@data-occludable-job-id]')

                cards_to_open = []
                skipped_cards = 0
                for offer in offersPerPage:
                    if remaining_applications(countApplied, run_cap, day_cap, store=self.store) <= 0:
                        prYellow("Reached the application cap. Stopping to protect the account.")
                        return
                    card, skip = self.inspect_search_card(offer)
                    sleep_human(self.pace(), "list_scan")
                    if skip:
                        self.record_card_skip(card, skip)
                        skipped_cards += 1
                        continue
                    if snippet_is_usable(getattr(card, "snippet", "") or ""):
                        offer_page = "https://www.linkedin.com/jobs/view/" + str(card.job_id)
                        card_details = {
                            "title": card.title,
                            "company": card.company,
                            "location": card.location,
                            "location_text": card.location,
                            "snippet": card.snippet,
                            "page_open": False,
                            "line": " | ".join(
                                filter(None, [card.title, card.company, card.location])
                            ),
                        }
                        if self.apply_fit_gate(card_details, card.job_id, offer_page, card=card):
                            skipped_cards += 1
                            continue
                        passed = getattr(self, "_card_fit_passed", None)
                        if passed is None:
                            self._card_fit_passed = set()
                        self._card_fit_passed.add(str(card.job_id))
                    cards_to_open.append(card)

                self.log_page_matches(page, len(offersPerPage), len(cards_to_open), skipped_cards)
                if not cards_to_open:
                    continue

                for card in cards_to_open:
                    if remaining_applications(countApplied, run_cap, day_cap, store=self.store) <= 0:
                        prYellow("Reached the application cap. Stopping to protect the account.")
                        return
                    jobID = card.job_id
                    offerPage = "https://www.linkedin.com/jobs/view/" + str(jobID)
                    try:
                        self.driver.get(offerPage)
                        self.wait_for_job_page()
                        self.browse_job_like_human()
                        countJobs += 1
                        if should_take_long_break(countJobs):
                            prYellow("Taking a short human break before the next jobs.")
                            sleep_human(self.pace(), "long_break")

                        details = self.getJobProperties(countJobs)
                        if not details.get("title"):
                            details["title"] = card.title
                        if not details.get("company"):
                            details["company"] = card.company
                        if not details.get("location"):
                            details["location"] = card.location
                        if getattr(card, "snippet", ""):
                            details["snippet"] = details.get("snippet") or card.snippet
                        details["location_text"] = details.get("location") or card.location
                        details["page_open"] = True
                        self.current_job_details = details
                        jobProperties = details["line"]
                        jobTitle = details["title"]
                        jobCompany = details["company"]
                        current_url = ""
                        try:
                            current_url = self.driver.current_url or ""
                        except WebDriverException:
                            current_url = offerPage
                        if "/login" in current_url or "authwall" in current_url.lower():
                            lineToWrite = jobProperties + " | * LinkedIn login or auth wall. Job: " + str(offerPage)
                            self.displayWriteResults(lineToWrite)
                            self.record_job(offerPage, "failed", "LinkedIn login or auth wall", details)
                            sleep_human(self.pace(), "skip")
                            continue

                        if self.job_identity_changed(card, jobTitle, jobCompany) and self.shouldSkipJob(jobTitle, jobCompany):
                            lineToWrite = jobProperties + " | * Skipped by title/company filter. Job: " + str(offerPage)
                            self.displayWriteResults(lineToWrite)
                            self.record_job(offerPage, "skipped_filter", "Skipped by title/company filter", details)
                            sleep_human(self.pace(), "skip")
                            continue

                        if should_skip_workplace(
                            details.get("workplace") or "",
                            details.get("location") or "",
                            getattr(card, "location", "") or "",
                        ):
                            lineToWrite = jobProperties + " | * Skipped by workplace filter. Job: " + str(offerPage)
                            self.displayWriteResults(lineToWrite)
                            self.record_job(offerPage, "skipped_filter", "Skipped by workplace filter", details)
                            sleep_human(self.pace(), "skip")
                            continue

                        kind, button = self.waitForApplyControl()

                        if not (jobTitle or "").strip() and kind == "missing":
                            self.save_job_load_debug(jobID)
                            lineToWrite = jobProperties + " | * Job page did not finish loading. Job: " + str(offerPage)
                            self.displayWriteResults(lineToWrite)
                            html_path = os.path.join("data", "job_load_" + str(jobID) + ".html")
                            screenshot = os.path.join("data", "job_load_" + str(jobID) + ".png")
                            self.record_job(
                                offerPage,
                                "page_timeout",
                                "Job page did not finish loading",
                                details,
                                screenshot_path=screenshot if os.path.isfile(screenshot) else "",
                                html_path=html_path if os.path.isfile(html_path) else "",
                            )
                            sleep_human(self.pace(), "skip")
                            continue

                        if not (jobTitle or "").strip() and kind == "easy_apply":
                            prYellow("Title missing; in-app apply is visible, applying anyway. Job: " + str(offerPage))

                        if kind == "already_applied":
                            lineToWrite = jobProperties + " | * Already applied. Job: " + str(offerPage)
                            self.displayWriteResults(lineToWrite)
                            self.record_job(offerPage, "already_applied", "Already applied", details)
                            sleep_human(self.pace(), "skip")
                            continue

                        if kind == "external":
                            lineToWrite = jobProperties + " | * Off-site apply, not Easy Apply. Job: " + str(offerPage)
                            self.displayWriteResults(lineToWrite)
                            self.record_job(offerPage, "failed", "Off-site apply, not Easy Apply", details)
                            sleep_human(self.pace(), "skip")
                            continue

                        if kind != "easy_apply" or button is None:
                            self.save_job_load_debug(jobID)
                            lineToWrite = jobProperties + " | * Easy Apply control not found. Job: " + str(offerPage)
                            self.displayWriteResults(lineToWrite)
                            html_path = os.path.join("data", "job_load_" + str(jobID) + ".html")
                            screenshot = os.path.join("data", "job_load_" + str(jobID) + ".png")
                            self.record_job(
                                offerPage,
                                "failed",
                                "Easy Apply control not found",
                                details,
                                screenshot_path=screenshot if os.path.isfile(screenshot) else "",
                                html_path=html_path if os.path.isfile(html_path) else "",
                            )
                            sleep_human(self.pace(), "skip")
                            continue

                        if str(jobID) not in getattr(self, "_card_fit_passed", set()) and self.apply_fit_gate(details, jobID, offerPage, card=card):
                            sleep_human(self.pace(), "skip")
                            continue

                        click_result = self.clickApplyControl(button)
                        if click_result == "external":
                            lineToWrite = jobProperties + " | * Off-site apply, not Easy Apply. Job: " + str(offerPage)
                            self.displayWriteResults(lineToWrite)
                            self.record_job(offerPage, "failed", "Off-site apply, not Easy Apply", details)
                            sleep_human(self.pace(), "skip")
                            continue
                        if click_result == "error":
                            self.save_job_load_debug(jobID)
                            lineToWrite = jobProperties + " | * Could not click Easy Apply. Job: " + str(offerPage)
                            self.displayWriteResults(lineToWrite)
                            html_path = os.path.join("data", "job_load_" + str(jobID) + ".html")
                            screenshot = os.path.join("data", "job_load_" + str(jobID) + ".png")
                            self.record_job(
                                offerPage,
                                "failed",
                                "Could not click Easy Apply",
                                details,
                                screenshot_path=screenshot if os.path.isfile(screenshot) else "",
                                html_path=html_path if os.path.isfile(html_path) else "",
                            )
                            sleep_human(self.pace(), "skip")
                            continue
                        result, applied = self.applyProcess(offerPage, jobID)
                        if applied:
                            countApplied += 1
                            record_application()
                            self.record_job(offerPage, "applied", result, details)
                            sleep_human(self.pace(), "after_apply")
                        else:
                            from linkedin_easy_apply.store import status_from_result

                            status = status_from_result(result)
                            if status in {"skipped_fit", "needs_review", "skipped_filter"}:
                                self.record_job(offerPage, status, result, details)
                            else:
                                screenshot, html_path = self.last_failure_artifacts
                                self.record_job(
                                    offerPage,
                                    "failed",
                                    result,
                                    details,
                                    screenshot_path=screenshot if screenshot and os.path.isfile(screenshot) else "",
                                    html_path=html_path if html_path and os.path.isfile(html_path) else "",
                                )
                            sleep_human(self.pace(), "skip")
                        self.displayWriteResults(jobProperties + " | " + result)
                    except WebDriverException as exc:
                        prRed("Browser connection lost: " + str(exc)[:200])
                        raise

            prYellow("Category: " + urlWords[0] + "," +urlWords[1]+ " applied: " + str(countApplied) +
                  " jobs out of " + str(countJobs) + ".")
        
        utils.donate(self)

    def getJobProperties(self, count):
        def first_text(selectors):
            for by, selector in selectors:
                try:
                    elements = self.driver.find_elements(by, selector)
                except WebDriverException:
                    continue
                for element in elements:
                    try:
                        raw = element.get_attribute("innerText") or element.text or ""
                    except WebDriverException:
                        continue
                    if raw.strip():
                        return " ".join(raw.split())
            return ""

        jobTitle = first_text([
            (By.CSS_SELECTOR, ".job-details-jobs-unified-top-card__job-title"),
            (By.CSS_SELECTOR, ".jobs-unified-top-card__job-title"),
            (By.CSS_SELECTOR, "h1.t-24"),
            (By.CSS_SELECTOR, "h1 a"),
            (By.CSS_SELECTOR, "h1"),
            (By.CSS_SELECTOR, "h2.t-24"),
            (By.CSS_SELECTOR, "[class*='job-title']"),
        ])
        jobCompany = first_text([
            (By.CSS_SELECTOR, ".job-details-jobs-unified-top-card__company-name"),
            (By.CSS_SELECTOR, ".jobs-unified-top-card__company-name"),
            (By.CSS_SELECTOR, "a[href*='/company/']"),
        ])
        if not jobTitle:
            try:
                parsed_title, parsed_company = self.parseDocumentTitle(self.driver.title)
            except WebDriverException:
                parsed_title, parsed_company = "", ""
            jobTitle = parsed_title
            if not jobCompany:
                jobCompany = parsed_company
        jobLocation = first_text([
            (By.CSS_SELECTOR, ".job-details-jobs-unified-top-card__primary-description-container"),
            (By.CSS_SELECTOR, ".jobs-unified-top-card__bullet"),
        ])
        jobWorkPlace = first_text([(By.CSS_SELECTOR, ".job-details-jobs-unified-top-card__workplace-type")])
        jobPostedDate = first_text([(By.CSS_SELECTOR, "time"), (By.CSS_SELECTOR, ".jobs-unified-top-card__posted-date")])
        jobApplications = first_text([(By.CSS_SELECTOR, ".jobs-unified-top-card__applicant-count")])
        jobDescription = first_text([
            (By.CSS_SELECTOR, "#job-details"),
            (By.CSS_SELECTOR, ".jobs-description__content"),
            (By.CSS_SELECTOR, ".jobs-box__html-content"),
            (By.CSS_SELECTOR, ".jobs-description-content__text"),
            (By.CSS_SELECTOR, "article.jobs-description"),
        ])
        line = " | ".join(map(str, [count, jobTitle, jobCompany, jobLocation, jobWorkPlace, jobPostedDate, jobApplications]))
        return {
            "line": line,
            "title": jobTitle,
            "company": jobCompany,
            "location": jobLocation,
            "workplace": jobWorkPlace,
            "snippet": "",
            "description": jobDescription,
        }

    @staticmethod
    def shouldSkipJob(title: str, company: str) -> bool:
        """Honor config allow/deny lists. Empty entries are ignored."""
        return should_skip_job(title, company)

    @staticmethod
    def job_identity_changed(card: SearchCard, title: str, company: str) -> bool:
        """True when the job page title/company differs from the already-filtered card."""
        page_title = (title or "").strip().lower()
        page_company = (company or "").strip().lower()
        card_title = (getattr(card, "title", "") or "").strip().lower()
        card_company = (getattr(card, "company", "") or "").strip().lower()
        if page_title and page_title != card_title:
            return True
        return bool(page_company and page_company != card_company)

    def log_page_matches(self, page: int, total: int, matched: int, skipped: int) -> None:
        line = (
            f"Search page {page + 1}: opening {matched} matching job(s) "
            f"out of {total} cards ({skipped} filtered before open)."
        )
        prYellow(line)
        self.displayWriteResults("\n " + line)

    def stored_job(self, job_id: str) -> dict | None:
        store = getattr(self, "store", None)
        if store is None or not job_id:
            return None
        getter = getattr(store, "get_job", None)
        if not callable(getter):
            return None
        try:
            row = getter(job_id)
        except (OSError, TypeError, ValueError):
            return None
        return row if isinstance(row, dict) else None

    def details_from_card(self, card: SearchCard) -> dict:
        line = " | ".join(
            part
            for part in [
                card.job_id or "unknown",
                card.title or "(no title)",
                card.company or "(no company)",
                card.location,
            ]
            if part
        )
        return {
            "line": line,
            "title": card.title,
            "company": card.company,
            "location": card.location,
            "location_text": card.location,
            "workplace": "",
            "snippet": card.snippet,
            "page_open": False,
        }

    def record_card_skip(self, card: SearchCard, skip: tuple[str, str]) -> None:
        status, reason = skip
        details = self.details_from_card(card)
        offer_page = (
            "https://www.linkedin.com/jobs/view/" + card.job_id if card.job_id else ""
        )
        target = offer_page or card.job_id or "unknown"
        line = details["line"] + " | * Skipped before open: " + reason + ". Job: " + target
        self.displayWriteResults(line)
        if offer_page:
            self.record_job(offer_page, status, "Skipped before open: " + reason, details)
        else:
            self.observe(line.strip(), card.job_id or None)

    def inspect_search_card(self, offer) -> tuple[SearchCard, tuple[str, str] | None]:
        """Read the list card and decide whether the job page may be opened."""
        job_id = ""
        try:
            job_id = normalize_job_id(offer.get_attribute("data-occludable-job-id") or "")
        except WebDriverException:
            job_id = ""
        stored = self.stored_job(job_id)
        stored_status = str((stored or {}).get("status") or "")
        if stored_status in {"applied", "already_applied"}:
            card = SearchCard(
                job_id=job_id,
                title=str((stored or {}).get("title") or ""),
                company=str((stored or {}).get("company") or ""),
                location=str((stored or {}).get("location") or ""),
                apply_kind="already_applied",
                source="store",
            )
            return card, card_skip_decision(card, applied_in_store=True)
        try:
            card = self.read_search_card(offer, job_id)
        except WebDriverException:
            card = SearchCard(job_id=job_id, source="error")
        if card is None:
            card = SearchCard(job_id=job_id, source="error")
        return card, card_skip_decision(card, applied_in_store=False)

    def read_search_card(self, offer, job_id: str = "") -> SearchCard:
        """Title, company, and Easy Apply from the search card light DOM."""
        self._scroll_search_card(offer)
        html, attr_id, signals = self._search_card_signals(offer)
        card = parse_search_card(html, job_id or attr_id)
        card = apply_card_fallbacks(card, **signals)
        if card.title:
            return card
        self._wait_for_search_card(offer)
        html, attr_id, signals = self._search_card_signals(offer)
        card = parse_search_card(html, job_id or attr_id or card.job_id)
        return apply_card_fallbacks(card, **signals)

    def _scroll_search_card(self, offer) -> None:
        try:
            self.driver.execute_script(
                "arguments[0].scrollIntoView({block:'center', inline:'nearest'});",
                offer,
            )
        except WebDriverException:
            return

    def _wait_for_search_card(self, offer, timeout: float = 2.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                html = offer.get_attribute("outerHTML") or ""
                aria = offer.get_attribute("aria-label") or ""
            except WebDriverException:
                return
            if parse_search_card(html).title or aria.strip():
                return
            time.sleep(0.2)

    def _search_card_signals(self, offer) -> tuple[str, str, dict]:
        html = ""
        job_id = ""
        aria_labels: list[str] = []
        extra_text = ""
        extra_hrefs: list[str] = []
        extra_class = ""
        extra_job_id = ""
        try:
            html = offer.get_attribute("outerHTML") or ""
            job_id = normalize_job_id(
                offer.get_attribute("data-occludable-job-id")
                or offer.get_attribute("data-job-id")
                or ""
            )
            extra_text = offer.text or ""
            extra_class = offer.get_attribute("class") or ""
            root_aria = offer.get_attribute("aria-label") or ""
            if root_aria:
                aria_labels.append(root_aria)
        except WebDriverException:
            pass
        try:
            containers = offer.find_elements(
                By.CSS_SELECTOR,
                ".job-card-container, [data-job-id], [data-view-name='job-card']",
            )
        except WebDriverException:
            containers = []
        for container in containers:
            try:
                extra_job_id = extra_job_id or normalize_job_id(
                    container.get_attribute("data-job-id") or ""
                )
                extra_class = extra_class + " " + (container.get_attribute("class") or "")
                label = container.get_attribute("aria-label") or ""
                if label:
                    aria_labels.append(label)
            except WebDriverException:
                continue
        try:
            links = offer.find_elements(
                By.CSS_SELECTOR,
                "a[href*='/jobs/view/'], a[href*='openSDUIApplyFlow']",
            )
        except WebDriverException:
            links = []
        for link in links:
            try:
                href = link.get_attribute("href") or ""
                if href:
                    extra_hrefs.append(href)
                label = link.get_attribute("aria-label") or ""
                if label:
                    aria_labels.append(label)
            except WebDriverException:
                continue
        return html, job_id, {
            "aria_labels": aria_labels,
            "extra_text": extra_text,
            "extra_hrefs": extra_hrefs,
            "extra_job_id": extra_job_id or job_id,
            "extra_class": extra_class,
        }

    def extract_job_description(self) -> str:
        """Visible job-page description used when search-card snippet is too short."""
        selectors = (
            "#job-details",
            ".jobs-description__content",
            ".jobs-box__html-content",
            ".jobs-description-content__text",
            "article.jobs-description",
            ".jobs-description",
        )
        for selector in selectors:
            try:
                elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
            except WebDriverException:
                continue
            for element in elements:
                try:
                    raw = element.get_attribute("innerText") or element.text or ""
                except WebDriverException:
                    continue
                text = " ".join(raw.split())
                if text:
                    return text[:1500]
        return ""

    def resolve_fit_snippet(self, details: dict | None) -> tuple[str, str]:
        from linkedin_easy_apply.job_fit import compact_text, snippet_is_usable

        info = details or {}
        card = compact_text(str(info.get("snippet") or ""))
        if snippet_is_usable(card):
            return card, "card"
        page = compact_text(str(info.get("description") or ""))
        if not snippet_is_usable(page):
            page = compact_text(self.extract_job_description())
        if snippet_is_usable(page):
            return page, "description"
        return (page or card), ("description" if page else "card")

    def decide_job_fit(self, details: dict | None, job_id: object | None = None):
        from linkedin_easy_apply.job_fit import evaluate_listing_fit

        info = details or {}
        snippet, source = self.resolve_fit_snippet(info)
        result = evaluate_listing_fit(
            title=str(info.get("title") or ""),
            company=str(info.get("company") or ""),
            snippet=snippet,
            source=source,
            store=getattr(self, "store", None),
        )
        self.last_fit = result.as_status()
        self.observe(result.log_line(), job_id)
        return result

    def apply_fit_gate(
        self,
        details: dict | None,
        job_id: object,
        offer_page: str,
        card: SearchCard | None = None,
    ) -> bool:
        """Return True when the listing should be skipped. Ollama down never skips the job."""
        info = dict(details or {})
        if card is not None:
            info.setdefault("title", card.title)
            info.setdefault("company", card.company)
            info.setdefault("location", card.location)
            if card.snippet:
                info["snippet"] = card.snippet
        result = self.decide_job_fit(info, job_id)
        if result.skip_job:
            self._pending_fit_score = result.fit
            line = info.get("line") or ""
            self.displayWriteResults(str(line) + " | " + result.log_line() + ". Job: " + str(offer_page))
            self.record_job(
                offer_page,
                "skipped_fit",
                result.log_line(),
                info,
                fit_score=result.fit,
            )
            self._pending_fit_score = None
            return True
        self._pending_fit_score = result.fit
        return False

    def dismissApplication(self) -> None:
        """Best-effort close of Easy Apply so an unanswered required skip does not submit."""
        dismiss_selectors = (
            "button[aria-label='Dismiss']",
            "button[data-test-modal-close-btn]",
            "button.artdeco-modal__dismiss",
        )
        clicked = False
        for selector in dismiss_selectors:
            button = self._first_displayed(self._find_css(self.driver, selector))
            if button is None:
                continue
            try:
                button.click()
                clicked = True
                break
            except WebDriverException:
                continue
        if not clicked:
            return
        sleep_human(self.pace(), "skip")
        confirm_labels = ("discard", "don't save", "dont save")
        for root in list(self.iter_webdriver_search_roots(self.driver)):
            for button in self._find_css(root, "button"):
                try:
                    label = " ".join(filter(None, [
                        button.text,
                        button.get_attribute("aria-label"),
                    ])).lower()
                except WebDriverException:
                    continue
                if any(token in label for token in confirm_labels):
                    try:
                        button.click()
                    except WebDriverException:
                        return
                    return

    @staticmethod
    def parseDocumentTitle(document_title: str) -> tuple[str, str]:
        """LinkedIn job tabs are usually 'Title | Company | LinkedIn'."""
        raw = (document_title or "").strip()
        lower = raw.lower()
        if not raw or "login" in lower or "authwall" in lower or "sign in" in lower:
            return "", ""
        parts = [part.strip() for part in raw.split("|")]
        parts = [part for part in parts if part and part.lower() != "linkedin"]
        if not parts or parts[0].lower() in {"feed", "home", "jobs"}:
            return "", ""
        if len(parts) >= 2:
            return parts[0], parts[1]
        return parts[0], ""

    def save_job_load_debug(self, job_id):
        os.makedirs("data", exist_ok=True)
        try:
            self.driver.save_screenshot(os.path.join("data", "job_load_" + str(job_id) + ".png"))
        except WebDriverException:
            pass
        try:
            with open(os.path.join("data", "job_load_" + str(job_id) + ".html"), "w", encoding="utf-8") as file:
                file.write(self.driver.page_source)
        except (OSError, WebDriverException):
            pass

    def wait_for_job_page(self):
        def page_ready(driver):
            title, _company = self.parseDocumentTitle(driver.title or "")
            if title:
                return True
            locators = (
                ".job-details-jobs-unified-top-card__job-title",
                ".jobs-unified-top-card__job-title",
                "h1.t-24",
                "#jobs-apply-button-id",
                "button.jobs-apply-button",
                "button[aria-label*='Easy Apply']",
                "button[aria-label*='Apply to']",
            )
            for selector in locators:
                try:
                    elements = driver.find_elements(By.CSS_SELECTOR, selector)
                except WebDriverException:
                    continue
                for element in elements:
                    try:
                        text = (element.get_attribute("innerText") or element.text or "").strip()
                        label = (element.get_attribute("aria-label") or "").strip()
                    except WebDriverException:
                        continue
                    if text or "apply" in label.lower():
                        return True
            return False

        try:
            self.wait.until(page_ready)
        except TimeoutException:
            pass
        sleep_human(self.pace(), "job_view")

    def browse_job_like_human(self):
        if self.pace() == "fast":
            return
        for _ in range(random.randint(1, 3)):
            try:
                self.driver.execute_script(
                    "window.scrollBy(0, arguments[0]);", random.randint(180, 520)
                )
            except WebDriverException:
                return
            sleep_human(self.pace(), "field")

    def type_human(self, field, text):
        field.click()
        field.send_keys(Keys.CONTROL, "a")
        for char in str(text):
            field.send_keys(char)
            sleep_human(self.pace(), "type_key")

    @staticmethod
    def classifyApplyControl(label: str = "", href: str = "", element_id: str = "", class_name: str = "") -> str:
        """Classify a job-page control as in-app apply, already applied, or off-site."""
        blob = " ".join(filter(None, [label, href, element_id, class_name])).lower()
        stripped = " ".join((label or "").split()).lower()
        if re.search(r"\b(already applied|application submitted|you(?:['’]ve| have)? applied)\b", blob):
            return "already_applied"
        if re.search(r"^applied\b", stripped):
            return "already_applied"
        if any(token in blob for token in ("company website", "apply on company", "offsite", "external site")):
            return "external"
        if "opensduiapplyflow=true" in blob or "easy apply" in blob or "linkedin apply" in blob:
            return "easy_apply"
        if (element_id or "") == "jobs-apply-button-id" or "jobs-apply-button" in (class_name or "").lower():
            if re.search(r"\bapplied\b", stripped):
                return "already_applied"
            return "easy_apply"
        if stripped.startswith("apply to") or stripped == "apply":
            return "easy_apply"
        return ""

    @staticmethod
    def _apply_element_text(element) -> tuple[str, str, str, str]:
        label = " ".join(filter(None, [
            element.text,
            element.get_attribute("aria-label"),
            element.get_attribute("innerText"),
            element.get_attribute("textContent"),
        ]))
        href = element.get_attribute("href") or ""
        element_id = element.get_attribute("id") or ""
        class_name = element.get_attribute("class") or ""
        return label, href, element_id, class_name

    def findApplyControl(self):
        locators = [
            (By.ID, "jobs-apply-button-id"),
            (By.CSS_SELECTOR, "button.jobs-apply-button"),
            (By.CSS_SELECTOR, ".jobs-apply-button--top-card button"),
            (By.CSS_SELECTOR, ".jobs-s-apply button"),
            (By.CSS_SELECTOR, "[class*='jobs-apply-button'] button"),
            (By.CSS_SELECTOR, "a[href*='openSDUIApplyFlow=true']"),
            (By.CSS_SELECTOR, "button[aria-label*='Easy Apply']"),
            (By.CSS_SELECTOR, "button[aria-label*='LinkedIn Apply']"),
            (By.CSS_SELECTOR, "button[aria-label*='Apply to']"),
            (By.CSS_SELECTOR, "[data-live-test-job-apply-button]"),
            (
                By.XPATH,
                "//button[contains(translate(@aria-label,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), 'easy apply') or contains(translate(@aria-label,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), 'linkedin apply') or contains(translate(@aria-label,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), 'apply to')]",
            ),
        ]
        seen = set()
        ranked = []
        for by, selector in locators:
            try:
                elements = self.driver.find_elements(by, selector)
            except WebDriverException:
                continue
            for element in elements:
                try:
                    key = element.id
                except WebDriverException:
                    continue
                if key in seen:
                    continue
                seen.add(key)
                try:
                    displayed = bool(element.is_displayed())
                    enabled = bool(element.is_enabled()) and element.get_attribute("aria-disabled") != "true"
                    label, href, element_id, class_name = self._apply_element_text(element)
                except WebDriverException:
                    continue
                kind = self.classifyApplyControl(label, href, element_id, class_name)
                if not kind:
                    continue
                ranked.append((0 if displayed else 1, 0 if enabled else 1, kind, element))

        ranked.sort(key=lambda item: (item[0], item[1]))
        for _displayed, _enabled, kind, element in ranked:
            if kind == "already_applied":
                return "already_applied", None
            if kind == "external":
                return "external", None
            if kind == "easy_apply":
                try:
                    tag = (element.tag_name or "").lower()
                    if tag not in {"button", "a"}:
                        inner = element.find_elements(By.CSS_SELECTOR, "button, a[href]")
                        if inner:
                            return "easy_apply", inner[0]
                except WebDriverException:
                    pass
                return "easy_apply", element
        return "missing", None

    def waitForApplyControl(self, timeout: float = 10):
        deadline = time.time() + timeout
        last = ("missing", None)
        while time.time() < deadline:
            last = self.findApplyControl()
            if last[0] in {"already_applied", "external"}:
                return last
            if last[0] == "easy_apply" and last[1] is not None:
                element = last[1]
                try:
                    disabled = element.get_attribute("disabled")
                    aria_disabled = element.get_attribute("aria-disabled")
                    if aria_disabled == "true" or disabled not in {None, "false", False, ""}:
                        time.sleep(0.35)
                        continue
                except WebDriverException:
                    time.sleep(0.35)
                    continue
                return last
            time.sleep(0.35)
        return last

    def clickApplyControl(self, button) -> str:
        handles = list(self.driver.window_handles)
        try:
            self.driver.execute_script(
                "arguments[0].scrollIntoView({block:'center', inline:'nearest'});",
                button,
            )
        except WebDriverException:
            pass
        sleep_human(self.pace(), "field")
        clicked = False
        try:
            button.click()
            clicked = True
        except WebDriverException:
            try:
                self.driver.execute_script("arguments[0].click();", button)
                clicked = True
            except WebDriverException:
                clicked = False
        if not clicked:
            return "error"
        try:
            WebDriverWait(self.driver, 4).until(
                lambda driver: len(driver.window_handles) > len(handles) or self.findApplicationContainer()
            )
        except TimeoutException:
            try:
                self.driver.execute_script("arguments[0].click();", button)
                WebDriverWait(self.driver, 4).until(
                    lambda driver: len(driver.window_handles) > len(handles) or self.findApplicationContainer()
                )
            except (TimeoutException, WebDriverException):
                pass
        if len(self.driver.window_handles) > len(handles):
            try:
                self.driver.switch_to.window(self.driver.window_handles[-1])
                self.driver.close()
                self.driver.switch_to.window(handles[0])
            except WebDriverException:
                pass
            return "external"
        if self.findApplicationContainer():
            return "modal"
        return "clicked"

    def easyApplyButton(self):
        kind, button = self.waitForApplyControl()
        return button if kind == "easy_apply" else False

    def getApplicationProgress(self):
        """Read progress without relying on LinkedIn's temporary ember IDs."""
        try:
            progress = self.driver.find_element(
                By.CSS_SELECTOR, "[role='progressbar'], progress"
            )
            value = progress.get_attribute("aria-valuenow") or progress.get_attribute("value")
            if value:
                return max(1, int(float(value)))
        except (NoSuchElementException, TypeError, ValueError):
            pass
        return 50

    def applyProcess(self, offerPage, jobID):
        """Advance a standard Easy Apply dialog and report fields needing user input."""
        self.last_failure_artifacts = ("", "")
        self._session_answers = []
        try:
            dialog = self.wait.until(lambda driver: self.findApplicationContainer())
            detection = getattr(self, "last_modal_detection", {})
            if detection.get("interop_host"):
                self.observe("Easy Apply interop host found", jobID)
            if detection.get("shadow_pierced"):
                self.observe("Easy Apply closed shadow root pierced", jobID)
            if detection.get("dialog_found"):
                self.observe("Easy Apply dialog found inside shadow root", jobID)
            self.observe("Easy Apply modal found", jobID)
            for _ in range(10):
                dialog = self.wait.until(lambda driver: self.findApplicationContainer())
                skip_reason = self.fillKnownFields(dialog, jobID) or ""
                if skip_reason:
                    self.dismissApplication()
                    return "* Skipped: " + skip_reason, False
                scroll_areas = dialog.find_elements(By.CSS_SELECTOR, ".artdeco-modal__content, [data-test-modal-content]")
                for scroll_area in scroll_areas or [dialog]:
                    self.driver.execute_script(
                        "arguments[0].scrollTop = arguments[0].scrollHeight;", scroll_area
                    )
                sleep_human(self.pace(), "apply_step")
                action = self.findApplicationAction(dialog, ("submit",))
                if action:
                    self.observe("Easy Apply action found: submit", jobID)
                    if not config.followCompanies:
                        checked = dialog.find_elements(By.CSS_SELECTOR, "input[id*='follow-company'][type='checkbox']:checked")
                        if checked:
                            self.driver.execute_script("arguments[0].click();", checked[0])
                    self.clickApplicationAction(action)
                    self.wait.until(lambda d: not self.findApplicationContainer()
                                    or d.find_elements(By.XPATH, "//*[contains(., 'Application submitted') or contains(., 'application was sent')]"))
                    self.commit_answer_memory()
                    return "* 🥳 Just Applied to this job: " + offerPage, True

                action = self.findApplicationAction(dialog, ("review", "next"))
                if not action:
                    reason = (
                        "Easy Apply modal was visible but no enabled Next, Review, "
                        "or Submit action was found"
                    )
                    self.observe(reason, jobID, "warning")
                    return self.applicationFailure(dialog, offerPage, jobID, reason), False
                self.observe(
                    "Easy Apply action found: " + (self.applicationAction(action) or "next"),
                    jobID,
                )
                self.clickApplicationAction(action)
                sleep_human(self.pace(), "apply_step")
                errors = dialog.find_elements(By.CSS_SELECTOR, "[role='alert'], .artdeco-inline-feedback__message")
                if any(e.is_displayed() and e.text.strip() for e in errors):
                    return self.applicationFailure(
                        dialog,
                        offerPage,
                        jobID,
                        "LinkedIn rejected one or more application answers",
                    ), False
            return self.applicationFailure(
                dialog,
                offerPage,
                jobID,
                "Easy Apply progression timed out after 10 steps",
            ), False
        except TimeoutException:
            reason = "Easy Apply modal detection timed out after the apply control was clicked"
            self.observe(reason, jobID, "warning")
            return self.applicationFailure(None, offerPage, jobID, reason), False
        except Exception as exc:  # noqa: BLE001 - isolate one job from unexpected DOM changes
            prYellow("Easy Apply error: " + str(exc)[:200])
            traceback.print_exc()
            dialog = self.findApplicationContainer()
            reason = "Easy Apply failed during modal progression: " + type(exc).__name__
            return self.applicationFailure(
                dialog if dialog else None,
                offerPage,
                jobID,
                reason,
            ), False

    def _shadow_root_of(self, element):
        """Read a closed shadow root via WebDriver. JS `el.shadowRoot` is null here."""
        try:
            return element.shadow_root
        except (NoSuchShadowRootException, WebDriverException, AttributeError):
            return None

    def _find_css(self, root, selector: str) -> list:
        if root is None:
            return []
        try:
            return list(root.find_elements(By.CSS_SELECTOR, selector))
        except (WebDriverException, AttributeError, TypeError):
            return []

    def iter_webdriver_search_roots(self, start, limit: int = 40):
        """Yield start plus nested closed shadow roots using element.shadow_root."""
        if start is None:
            return
        queue = [start]
        seen: set[int] = set()
        yielded = 0
        while queue and yielded < limit:
            node = queue.pop(0)
            ident = id(node)
            if ident in seen:
                continue
            seen.add(ident)
            yield node
            yielded += 1
            shadow = self._shadow_root_of(node)
            if shadow is not None:
                queue.append(shadow)
            for descendant in self._find_css(node, "*"):
                nested = self._shadow_root_of(descendant)
                if nested is not None:
                    queue.append(nested)

    def _container_facts(self, root) -> dict:
        texts: list[str] = []
        actions: list[str] = []
        field_count = 0
        has_progress = False
        role = ""
        tag_name = ""
        aria_modal = ""
        try:
            tag_name = getattr(root, "tag_name", "") or ""
            role = root.get_attribute("role") or ""
            aria_modal = root.get_attribute("aria-modal") or ""
            texts.append(root.text or "")
        except (WebDriverException, AttributeError):
            pass
        for search in self.iter_webdriver_search_roots(root):
            try:
                texts.append(getattr(search, "text", None) or "")
            except (WebDriverException, AttributeError):
                pass
            for button in self._find_css(search, ACTION_BUTTON_SELECTOR):
                try:
                    name = self.applicationAction(button)
                except WebDriverException:
                    continue
                if name:
                    actions.append(name)
            for selector in FIELD_SELECTORS:
                field_count += len(self._find_css(search, selector))
            if self._find_css(search, PROGRESS_SELECTOR):
                has_progress = True
        return {
            "role": role,
            "tag_name": tag_name,
            "aria_modal": aria_modal,
            "text": " ".join(texts),
            "has_progress": has_progress,
            "field_count": field_count,
            "action_names": tuple(dict.fromkeys(actions)),
        }

    def _first_displayed(self, elements):
        for element in elements:
            try:
                if element.is_displayed():
                    return element
            except WebDriverException:
                continue
        return None

    def _container_in_closed_tree(self, host):
        dialog_hits = []
        scored = []
        for root in self.iter_webdriver_search_roots(host):
            for selector in DIALOG_SELECTORS:
                dialog_hits.extend(self._find_css(root, selector))
            if is_application_container_candidate(**self._container_facts(root)):
                scored.append(root)
        displayed_dialog = self._first_displayed(dialog_hits)
        if displayed_dialog is not None:
            return displayed_dialog
        ranked = []
        for node in scored:
            try:
                testid = node.get_attribute("data-testid") or ""
            except (WebDriverException, AttributeError):
                testid = ""
            if testid == "interop-shadowdom":
                continue
            try:
                if getattr(node, "tag_name", None) and node.is_displayed():
                    ranked.append(node)
                    continue
            except (WebDriverException, AttributeError):
                pass
            child = self._first_displayed(self._find_css(node, "*"))
            if child is not None:
                ranked.append(child)
        if ranked:
            return ranked[0]
        shadow = self._shadow_root_of(host)
        if shadow is not None:
            return self._first_displayed(self._find_css(shadow, "*"))
        return None

    def findApplicationContainer(self):
        # Closed interop shadow first. JS cannot read host.shadowRoot.
        for host in self._find_css(self.driver, INTEROP_HOST_SELECTOR):
            self.last_modal_detection = {"interop_host": True}
            if self._shadow_root_of(host) is not None:
                self.last_modal_detection["shadow_pierced"] = True
            found = self._container_in_closed_tree(host)
            if found is not None:
                self.last_modal_detection["dialog_found"] = True
                return found
        try:
            element = self.driver.execute_script(
                DEEP_APPLICATION_CONTAINER_JS,
                list(DIALOG_SELECTORS),
            )
            if element is not None:
                return element
        except WebDriverException:
            pass
        displayed = self._first_displayed(self._find_css(self.driver, self.modalSelector))
        if displayed is not None:
            return displayed
        # Older light-DOM Easy Apply: heading plus footer action, never carousel Next.
        xpath = (
            "//*[self::h1 or self::h2 or self::h3]"
            "[contains(translate(normalize-space(.), "
            "'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), 'apply to')]"
            "/ancestor::div[.//button["
            "(translate(normalize-space(@aria-label),"
            "'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')='next' or "
            "contains(translate(@aria-label,"
            "'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'next') or "
            "contains(translate(@aria-label,"
            "'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'continue') or "
            "contains(translate(@aria-label,"
            "'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'review') or "
            "contains(translate(@aria-label,"
            "'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'submit') or "
            "normalize-space(.)='Next' or contains(normalize-space(.),'Continue')) "
            "and not(@data-testid='carousel-inline-right-button') "
            "and not(@data-testid='carousel-inline-left-button')"
            "]][1]"
        )
        try:
            displayed = self._first_displayed(self.driver.find_elements(By.XPATH, xpath))
        except WebDriverException:
            displayed = None
        return displayed if displayed is not None else False

    @staticmethod
    def applicationAction(button):
        try:
            text = button.text or ""
            aria_label = button.get_attribute("aria-label") or ""
            testid = button.get_attribute("data-testid") or ""
            class_name = button.get_attribute("class") or ""
        except (WebDriverException, AttributeError):
            return classify_application_action(getattr(button, "text", "") or "", "")
        return classify_application_action(text, aria_label, testid, class_name)

    def _action_in_closed_tree(self, root, wanted):
        wanted_list = list(wanted)
        found: dict[str, object] = {}
        for search in self.iter_webdriver_search_roots(root):
            for button in self._find_css(search, ACTION_BUTTON_SELECTOR):
                try:
                    if not button.is_displayed() or not button.is_enabled():
                        continue
                    if button.get_attribute("aria-disabled") == "true":
                        continue
                    name = self.applicationAction(button)
                except WebDriverException:
                    continue
                if name in wanted_list and name not in found:
                    found[name] = button
        for name in wanted_list:
            if name in found:
                return found[name]
        return None

    def findApplicationAction(self, dialog, wanted):
        """Locate modal Next/Review/Submit, including footer siblings, skipping carousel."""
        roots = []
        if dialog is not None:
            roots.append(dialog)
            try:
                roots.append(dialog.find_element(By.XPATH, ".."))
            except (NoSuchElementException, WebDriverException, AttributeError):
                pass
        for root in roots:
            button = self._action_in_closed_tree(root, wanted)
            if button is not None:
                return button
        try:
            button = self.driver.execute_script(
                DEEP_APPLICATION_ACTION_JS,
                dialog,
                list(wanted),
            )
            if button is not None:
                return button
        except WebDriverException:
            pass
        expressions = {
            "submit": (
                "contains(translate(@aria-label,'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
                "'abcdefghijklmnopqrstuvwxyz'),'submit application') or "
                "contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
                "'abcdefghijklmnopqrstuvwxyz'),'submit application')"
            ),
            "review": (
                "contains(translate(@aria-label,'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
                "'abcdefghijklmnopqrstuvwxyz'),'review') or "
                "contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
                "'abcdefghijklmnopqrstuvwxyz'),'review')"
            ),
            "next": (
                "(translate(normalize-space(@aria-label),'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
                "'abcdefghijklmnopqrstuvwxyz')='next' or "
                "contains(translate(@aria-label,'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
                "'abcdefghijklmnopqrstuvwxyz'),'next') or "
                "contains(translate(@aria-label,'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
                "'abcdefghijklmnopqrstuvwxyz'),'continue') or "
                "normalize-space(.)='Next' or contains(normalize-space(.),'Continue')) "
                "and not(@data-testid='carousel-inline-right-button') "
                "and not(@data-testid='carousel-inline-left-button')"
            ),
        }
        search_nodes = list(roots) if roots else []
        for action_name in wanted:
            xpath = ".//button[" + expressions[action_name] + "]"
            for node in search_nodes:
                try:
                    for button in node.find_elements(By.XPATH, xpath):
                        if (
                            button.is_displayed()
                            and button.is_enabled()
                            and self.applicationAction(button) == action_name
                        ):
                            return button
                except (WebDriverException, AttributeError):
                    continue
        return None

    def clickApplicationAction(self, button):
        try:
            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
            self.driver.execute_script("arguments[0].click();", button)
        except WebDriverException:
            # Re-raise with the precise operation instead of a generic modal failure.
            raise RuntimeError("LinkedIn replaced the selected application action before it could be clicked")

    def snapshot_root(self, dialog):
        if dialog:
            return dialog
        try:
            return self.driver.execute_script("return document.documentElement")
        except WebDriverException:
            return None

    def collect_form_state(self, dialog):
        from linkedin_easy_apply.page_snapshot import COLLECT_JS, FIELD_SELECTOR, VISIBLE_TEXT_JS

        starts = []
        root = self.snapshot_root(dialog)
        if root is not None:
            starts.append(root)
        starts.extend(self._find_css(self.driver, INTEROP_HOST_SELECTOR))

        fields = []
        snapshot_parts: list[str] = []
        seen = set()
        for start in starts:
            for node in self.iter_webdriver_search_roots(start):
                metas, elements = None, None
                try:
                    metas, elements = self.driver.execute_script(COLLECT_JS, node, FIELD_SELECTOR)
                except WebDriverException:
                    metas, elements = self._collect_fields_via_webdriver(node)
                for meta, element in zip(metas or [], elements or []):
                    if not isinstance(meta, dict):
                        continue
                    try:
                        key = element.id
                    except (WebDriverException, AttributeError):
                        continue
                    if key in seen:
                        continue
                    seen.add(key)
                    item = dict(meta)
                    item["_element"] = element
                    fields.append(item)
                try:
                    text = self.driver.execute_script(VISIBLE_TEXT_JS, node, 6000) or ""
                except WebDriverException:
                    text = getattr(node, "text", "") or ""
                if text:
                    snapshot_parts.append(text)
        snapshot = " ".join(dict.fromkeys(snapshot_parts))[:6000]
        return fields, snapshot

    def _collect_fields_via_webdriver(self, node):
        from linkedin_easy_apply.page_snapshot import FIELD_SELECTOR, field_kind_from_attrs

        elements = self._find_css(node, FIELD_SELECTOR)
        metas = []
        for element in elements:
            try:
                question = " ".join(filter(None, [
                    element.get_attribute("aria-label"),
                    element.get_attribute("placeholder"),
                    element.get_attribute("name"),
                ]))
                meta = {
                    "question": question,
                    "kind": field_kind_from_attrs(
                        element.tag_name or "",
                        element.get_attribute("role") or "",
                        element.get_attribute("type") or "",
                        element.get_attribute("contenteditable") or "",
                    ),
                    "type": element.get_attribute("type") or "",
                    "tag": (element.tag_name or "").lower(),
                    "id": element.get_attribute("id") or "",
                    "name": element.get_attribute("name") or "",
                    "value": (element.get_attribute("value") or "").strip(),
                    "checked": element.get_attribute("aria-checked") == "true",
                    "required": element.get_attribute("aria-required") == "true",
                    "options": [],
                }
            except WebDriverException:
                continue
            metas.append(meta)
        return metas, elements

    def fill_control(self, field: dict, value: str) -> bool:
        from linkedin_easy_apply.page_snapshot import match_option

        element = field.get("_element")
        raw = " ".join(str(value or "").split())
        if element is None or not raw:
            return False
        kind = str(field.get("kind") or "text")
        try:
            if kind == "select":
                chosen = match_option(raw, list(field.get("options") or []))
                if not chosen:
                    return False
                Select(element).select_by_visible_text(chosen)
                sleep_human(self.pace(), "field")
                return True
            if kind == "radio":
                chosen = match_option(raw, list(field.get("options") or [])) or raw
                options = self.driver.execute_script(
                    "const el = arguments[0];"
                    "const group = el.closest('fieldset,[role=radiogroup],[role=group]') || el;"
                    "return Array.from(group.querySelectorAll('[role=radio], input[type=radio]'));",
                    element,
                ) or []
                target = chosen.lower()
                for option in options:
                    label = " ".join(filter(None, [
                        option.text,
                        option.get_attribute("aria-label"),
                        option.get_attribute("value"),
                    ])).lower()
                    if target == label or target in label or label.startswith(target):
                        sleep_human(self.pace(), "field")
                        self.driver.execute_script("arguments[0].click();", option)
                        return True
                return False
            if kind == "checkbox":
                wanted = raw.lower() in {"yes", "true", "1", "on"}
                checked = bool(field.get("checked"))
                if wanted != checked:
                    sleep_human(self.pace(), "field")
                    self.driver.execute_script("arguments[0].click();", element)
                return True
            if kind in {"text", "textarea", "tel", "email", "number"}:
                current = (element.get_attribute("value") or "").strip()
                if current:
                    return False
                element.click()
                element.send_keys(Keys.CONTROL, "a")
                self.type_human(element, raw)
                element.send_keys(Keys.TAB)
                sleep_human(self.pace(), "field")
                return True
        except (WebDriverException, NoSuchElementException, ValueError):
            return False
        return False

    def approved_value_for_field(self, field: dict) -> tuple[str, str]:
        """Return (value, source_id) from approved SQLite memory, if any."""
        store = getattr(self, "store", None)
        if store is None:
            return "", ""
        question = str(field.get("question") or "")
        if not question.strip():
            return "", ""
        retrieve = getattr(store, "retrieve_approved_answers", None)
        if not callable(retrieve):
            return "", ""
        try:
            matches = retrieve([{"question": question, "kind": field.get("kind") or ""}], limit=1) or []
        except (OSError, TypeError, ValueError):
            return "", ""
        if not matches:
            return "", ""
        item = matches[0]
        value = str(item.get("value") or "").strip()
        source_id = str(item.get("source_id") or "")
        if not value:
            return "", ""
        note = str(item.get("note") or "").strip()
        if note:
            field["_inference_note"] = note
        elif str(item.get("match") or "") == "interpolated":
            field["_inference_note"] = "Inferred from related approved answer"
        kind = str(field.get("kind") or "").lower()
        options = [str(option) for option in field.get("options") or [] if str(option).strip()]
        if kind in {"select", "radio"} and options:
            from linkedin_easy_apply.page_snapshot import match_option

            chosen = match_option(value, options)
            if not chosen:
                return "", ""
            return chosen, source_id
        return value, source_id

    def mapped_value_for_field(self, field: dict) -> str:
        from linkedin_easy_apply.facts import mapped_years_experience
        from linkedin_easy_apply.inference import skill_years_for_question

        question = str(field.get("question") or "")
        kind = str(field.get("kind") or "")
        question_l = question.lower()
        if kind in {"tel"} or "phone" in question_l:
            return str(getattr(config, "phone_number", "") or "")
        if "city" in question_l or "location" in question_l:
            return str(getattr(config, "application_city", "") or "")
        years_map = mapped_years_experience(getattr(config, "years_experience", {}) or {})
        inferred = skill_years_for_question(question, years_map, kind=kind)
        if inferred is not None:
            if inferred.note:
                field["_inference_note"] = inferred.note
            return inferred.value
        if kind in {"radio", "select", "checkbox"}:
            return self.yesNoAnswerForQuestion(question) or ""
        return self.yesNoAnswerForQuestion(question) or ""

    def remember_session_answer(self, payload: dict) -> None:
        fills = getattr(self, "_session_answers", None)
        if fills is None:
            self._session_answers = []
            fills = self._session_answers
        fills.append(payload)

    def commit_answer_memory(self) -> None:
        store = getattr(self, "store", None)
        fills = getattr(self, "_session_answers", None) or []
        if store is not None and fills:
            recorder = getattr(store, "record_successful_fills", None)
            if callable(recorder):
                recorder(fills)
        self._session_answers = []

    def fill_llm_fields(self, fields: list, snapshot: str, job_id: object | None = None) -> None:
        from linkedin_easy_apply.facts import applicant_facts
        from linkedin_easy_apply.llm import answer_unanswered
        from linkedin_easy_apply.llm import status as llm_status
        from linkedin_easy_apply.page_snapshot import unanswered_fields

        pending = []
        for field in fields:
            if field.get("_answered"):
                continue
            meta = {key: value for key, value in field.items() if key != "_element"}
            if unanswered_fields([meta]):
                pending.append(field)
        if not pending:
            self.observe("Ollama skipped: no unanswered fields", job_id)
            return
        info = llm_status()
        if not info.get("ready"):
            self.observe("Ollama skipped: local model is not ready", job_id)
            return
        questions = []
        for field in pending:
            field["_llm_considered"] = True
            questions.append({
                "question": field.get("question") or "",
                "kind": field.get("kind") or "",
                "options": list(field.get("options") or []),
                "required": bool(field.get("required")),
            })
        images = None
        if "vision" in str(info.get("model") or "").lower():
            try:
                import base64
                images = [base64.b64encode(self.driver.get_screenshot_as_png()).decode("ascii")]
            except (WebDriverException, TypeError, ValueError):
                images = None
        store = getattr(self, "store", None)
        try:
            self.observe(
                "Ollama invoked for " + str(len(questions)) + " unanswered field(s)",
                job_id,
            )
            facts = applicant_facts(store=store, questions=questions)
            self.observe(
                "Ollama approved-answer matches: "
                + str(len(facts.get("approved_answers") or [])),
                job_id,
            )
            answers = answer_unanswered(
                facts,
                snapshot,
                questions,
                images=images,
                store=store,
            )
        except (OSError, ValueError) as exc:
            self.observe(
                "Ollama skipped: request failed (" + type(exc).__name__ + ")",
                job_id,
                "warning",
            )
            return
        applied_count = 0
        rejected_count = 0
        for item in answers:
            question = item.get("question") or ""
            target = None
            target_index = None
            needle = " ".join(question.lower().split())
            for index, field in enumerate(pending):
                hay = " ".join(str(field.get("question") or "").lower().split())
                if hay and (needle == hay or needle in hay or hay in needle):
                    target = field
                    target_index = index
                    break
            if target:
                target["_llm_value"] = item.get("value") or ""
            if target and self.fill_control(target, item.get("value") or ""):
                target["_answered"] = True
                target["value"] = target["_llm_value"]
                if target_index is not None:
                    pending.pop(target_index)
                applied_count += 1
                source_ids = list(item.get("source_ids") or [])
                if item.get("inferred"):
                    from linkedin_easy_apply.inference import log_question_label

                    self.observe("Inferred: " + log_question_label(question), job_id)
                self.remember_session_answer({
                    "question": question,
                    "value": item.get("value") or "",
                    "source": "llm",
                    "source_ids": source_ids,
                    "already_approved": bool(source_ids),
                    "field_kind": str(target.get("kind") or ""),
                })
            else:
                rejected_count += 1
        self.observe(
            "Ollama answers accepted="
            + str(len(answers))
            + ", applied="
            + str(applied_count)
            + ", rejected="
            + str(rejected_count),
            job_id,
        )

    def fillKnownFields(self, dialog, job_id: object | None = None):
        fields, snapshot = self.collect_form_state(dialog)
        self.observe("Application fields collected: " + str(len(fields)), job_id)
        for field in fields:
            question = str(field.get("question") or "").lower()
            element = field.get("_element")
            if field.get("kind") == "select" and element is not None and "phonecountry" in (
                question + str(field.get("id") or "") + str(field.get("name") or "")
            ).lower():
                try:
                    Select(element).select_by_value("urn:li:country:" + config.country_code.lower())
                except (NoSuchElementException, ValueError, WebDriverException):
                    pass

        city = getattr(config, "application_city", "")
        if city:
            city_field = self.findFieldByLabel(dialog, ("location", "city")) if dialog else None
            if city_field is None:
                for field in fields:
                    question = str(field.get("question") or "").lower()
                    if "city" in question or "location" in question:
                        city_field = field.get("_element")
                        break
            if city_field and not (city_field.get_attribute("value") or "").strip():
                try:
                    city_field.click()
                    city_field.send_keys(Keys.CONTROL, "a")
                    self.type_human(city_field, city)
                    sleep_human(self.pace(), "field")
                    try:
                        suggestion = WebDriverWait(self.driver, 5).until(EC.element_to_be_clickable((
                            By.CSS_SELECTOR,
                            "[role='listbox'] [role='option'], .basic-typeahead__selectable"
                        )))
                        suggestion.click()
                    except TimeoutException:
                        city_field.send_keys(Keys.ARROW_DOWN)
                        city_field.send_keys(Keys.ENTER)
                    city_field.send_keys(Keys.TAB)
                except WebDriverException:
                    pass

        self.attachResume(dialog)
        approved_count = 0
        mapped_count = 0
        for field in fields:
            if str(field.get("kind") or "") == "file":
                continue
            approved, source_id = self.approved_value_for_field(field)
            mapped = self.mapped_value_for_field(field)
            field["_mapped_value_present"] = bool(mapped)
            if approved and self.fill_control(field, approved):
                field["_approved_fill"] = True
                field["_answered"] = True
                field["value"] = approved
                approved_count += 1
                if str(field.get("kind") or "") in {"radio", "checkbox"}:
                    field["checked"] = str(approved).lower() in {"yes", "true", "1", "on"}
                self._log_inference_notes(field, job_id)
                self.remember_session_answer({
                    "question": str(field.get("question") or ""),
                    "value": approved,
                    "source": "manual",
                    "already_approved": True,
                    "source_ids": [source_id] if source_id else [],
                    "field_kind": str(field.get("kind") or ""),
                })
                continue
            if mapped and self.fill_control(field, mapped):
                field["_answered"] = True
                field["value"] = mapped
                mapped_count += 1
                if str(field.get("kind") or "") in {"radio", "checkbox"}:
                    field["checked"] = str(mapped).lower() in {"yes", "true", "1", "on"}
                self._log_inference_notes(field, job_id)
                self.remember_session_answer({
                    "question": str(field.get("question") or ""),
                    "value": mapped,
                    "source": "mapped",
                    "mapped": True,
                    "field_kind": str(field.get("kind") or ""),
                })
        self.observe("Approved memory applied: " + str(approved_count), job_id)
        self.observe("Mapped answers applied: " + str(mapped_count), job_id)
        self.fill_llm_fields(fields, snapshot, job_id)
        self.fill_conservative_inferences(fields, job_id)
        from linkedin_easy_apply.question_capture import append_questions, remember_questions

        details = getattr(self, "current_job_details", {}) or {}
        if getattr(self, "store", None) is not None:
            remembered = remember_questions(
                self.store,
                fields,
                job_id=job_id or "",
                title=str(details.get("title") or ""),
                company=str(details.get("company") or ""),
            )
            self.observe("Screening questions remembered: " + str(remembered), job_id)
        else:
            captured_count = append_questions(
                fields,
                job_id=job_id or "",
                title=str(details.get("title") or ""),
                company=str(details.get("company") or ""),
            )
            self.observe("Fallback questions captured: " + str(captured_count), job_id)
        from linkedin_easy_apply.page_snapshot import unanswered_required_reason

        skip_reason = unanswered_required_reason(fields)
        if skip_reason:
            self.observe("Skipped sensitive unanswered", job_id, "warning")
            self.observe(skip_reason, job_id, "warning")
        return skip_reason

    def _log_inference_notes(self, field: dict, job_id: object | None = None) -> None:
        from linkedin_easy_apply.inference import log_question_label

        note = str(field.get("_inference_note") or "").strip()
        if not note:
            return
        self.observe(note, job_id)
        label = log_question_label(str(field.get("question") or ""))
        if label and not note.casefold().startswith("inferred:"):
            self.observe("Inferred: " + label, job_id)

    def fill_conservative_inferences(self, fields: list, job_id: object | None = None) -> None:
        from linkedin_easy_apply.facts import mapped_years_experience
        from linkedin_easy_apply.inference import conservative_inferred_value

        years = mapped_years_experience(getattr(config, "years_experience", {}) or {})
        for field in fields:
            if field.get("_answered") or str(field.get("kind") or "") == "file":
                continue
            if not field.get("required"):
                continue
            inferred = conservative_inferred_value(field, years)
            if inferred is None or not inferred.value:
                continue
            if inferred.note:
                field["_inference_note"] = inferred.note
            if not self.fill_control(field, inferred.value):
                continue
            field["_answered"] = True
            field["_mapped_value_present"] = True
            field["value"] = inferred.value
            if str(field.get("kind") or "") in {"radio", "checkbox"}:
                field["checked"] = str(inferred.value).lower() in {"yes", "true", "1", "on"}
            self._log_inference_notes(field, job_id)
            self.remember_session_answer({
                "question": str(field.get("question") or ""),
                "value": inferred.value,
                "source": "mapped",
                "mapped": True,
                "field_kind": str(field.get("kind") or ""),
            })

    @staticmethod
    def isResumeUploadField(metadata: str, element_id: str = "", element_name: str = "") -> bool:
        text = " ".join(filter(None, [metadata, element_id, element_name])).lower()
        if any(word in text for word in ("cover letter", "coverletter", "supporting document")):
            return False
        return any(word in text for word in ("resume", "cv", "curriculum"))

    def attachResume(self, dialog):
        resume_path = resolved_resume_path(store=getattr(self, "store", None)) or getattr(
            config, "resume_path", ""
        )
        if not resume_path or not os.path.isfile(resume_path):
            return
        abs_path = os.path.abspath(resume_path)
        fields = []
        for start in filter(None, [self.snapshot_root(dialog), *self._find_css(self.driver, INTEROP_HOST_SELECTOR)]):
            for root in self.iter_webdriver_search_roots(start):
                from linkedin_easy_apply.page_snapshot import COLLECT_JS
                try:
                    _metas, elements = self.driver.execute_script(COLLECT_JS, root, "input[type='file']")
                    fields.extend(list(elements or []))
                except WebDriverException:
                    fields.extend(self._find_css(root, "input[type='file']"))
        if not fields and dialog:
            try:
                fields = dialog.find_elements(By.CSS_SELECTOR, "input[type='file']")
            except WebDriverException:
                fields = []
        targets = []
        for field in fields:
            try:
                metadata = self.fieldMetadata(field)
                if self.isResumeUploadField(
                    metadata,
                    field.get_attribute("id") or "",
                    field.get_attribute("name") or "",
                ):
                    targets.append(field)
            except WebDriverException:
                continue
        if not targets and len(fields) == 1:
            targets = fields
        for field in targets:
            try:
                field.send_keys(abs_path)
            except WebDriverException:
                continue

    @staticmethod
    def yesNoAnswerForQuestion(question):
        from linkedin_easy_apply.facts import mapped_yes_no_rules

        normalized = " ".join(question.lower().replace("‑", "-").split())
        for keywords, answer in mapped_yes_no_rules(getattr(config, "yes_no_answers", [])):
            if all(keyword.lower() in normalized for keyword in keywords):
                return answer
        return None

    def fieldMetadata(self, field):
        return self.driver.execute_script("""
            const el = arguments[0];
            const labels = Array.from(el.labels || []).map(x => x.innerText).join(' ');
            const group = el.closest('[data-testid="text-input"], .jobs-easy-apply-form-element, .fb-dash-form-element, .form-group');
            return [labels, el.placeholder, el.getAttribute('aria-label'), el.name,
                    group ? group.innerText.split('\\n')[0] : ''].filter(Boolean).join(' ').toLowerCase();
        """, field)

    def findFieldByLabel(self, dialog, words):
        """Find a visible field using its browser-associated label or nearest form group."""
        starts = [dialog] if dialog is not None else []
        starts.extend(self._find_css(self.driver, INTEROP_HOST_SELECTOR))
        for start in starts:
            for root in self.iter_webdriver_search_roots(start):
                for field in self._find_css(
                    root,
                    "input:not([type='hidden']), textarea, [role='combobox'], [role='textbox']",
                ):
                    try:
                        metadata = self.fieldMetadata(field)
                        if any(word in metadata for word in words) and field.is_displayed():
                            return field
                    except WebDriverException:
                        continue
        return None

    def _serialize_shadow_markup(self, dialog) -> str:
        """Serialize closed-shadow modal markup that page_source omits."""
        chunks: list[str] = []
        starts = []
        if dialog is not None:
            starts.append(dialog)
        starts.extend(self._find_css(self.driver, INTEROP_HOST_SELECTOR))
        seen: set[int] = set()
        for start in starts:
            for root in self.iter_webdriver_search_roots(start):
                ident = id(root)
                if ident in seen:
                    continue
                seen.add(ident)
                html = ""
                try:
                    html = self.driver.execute_script(
                        "if (!arguments[0]) return '';"
                        "if (arguments[0].outerHTML) return arguments[0].outerHTML;"
                        "if (arguments[0].innerHTML) return arguments[0].innerHTML;"
                        "return '';",
                        root,
                    ) or ""
                except WebDriverException:
                    html = ""
                if html:
                    chunks.append(html)
        return "\n".join(chunks)

    def applicationFailure(self, dialog, offerPage, jobID, fallback_reason: str = ""):
        from linkedin_easy_apply.diagnostics import artifact_stem, sanitize_html

        os.makedirs("data", exist_ok=True)
        stem = artifact_stem("easy_apply_failure", jobID)
        screenshot_path = os.path.join("data", stem + ".png")
        html_path = os.path.join("data", stem + ".html")
        masked = []
        try:
            masked = self.driver.execute_script(
                """
                const roots = [document];
                for (let i = 0; i < roots.length; i++) {
                  for (const el of roots[i].querySelectorAll("*")) {
                    if (el.shadowRoot) roots.push(el.shadowRoot);
                  }
                }
                return roots.flatMap(root => Array.from(root.querySelectorAll(
                    "input:not([type='hidden']), textarea"
                  )).map(el => {
                    const prior = el.value;
                    if (prior) el.value = "[REDACTED]";
                    return [el, prior];
                  })
                );
                """
            ) or []
            self.driver.save_screenshot(screenshot_path)
        except WebDriverException:
            screenshot_path = ""
        finally:
            if masked:
                try:
                    self.driver.execute_script(
                        "for (const [el, value] of arguments[0]) el.value = value;",
                        masked,
                    )
                except WebDriverException:
                    pass
        try:
            page_html = sanitize_html(self.driver.page_source)
            with open(html_path, "w", encoding="utf-8") as file:
                file.write(page_html)
        except (OSError, WebDriverException):
            html_path = ""
        self.last_failure_artifacts = (screenshot_path, html_path)
        shadow_html = ""
        try:
            shadow_html = sanitize_html(self._serialize_shadow_markup(dialog))
        except WebDriverException:
            shadow_html = ""
        if shadow_html:
            try:
                modal_path = os.path.join("data", stem + "_modal.html")
                with open(modal_path, "w", encoding="utf-8") as file:
                    file.write(shadow_html)
            except OSError:
                pass
        alerts = []
        if dialog:
            for element in self._find_css(dialog, "[role='alert'], .artdeco-inline-feedback__message"):
                try:
                    text = " ".join(element.text.split())
                except WebDriverException:
                    continue
                if text and text not in alerts:
                    alerts.append(text)
        if fallback_reason:
            reason = fallback_reason
            if alerts:
                reason = fallback_reason + "; " + "; ".join(alerts[:3])
        else:
            reason = "; ".join(alerts[:5]) or (
                "LinkedIn requires an unanswered field before this application can continue"
            )
        return "* 🥵 Couldn't apply: " + reason + ". Link: " + offerPage

    def displayWriteResults(self,lineToWrite: str):
        try:
            print(lineToWrite)
            utils.writeResults(lineToWrite)
        except Exception as e:  # noqa: BLE001 - reporting must not terminate the run
            prRed("Error in DisplayWriteResults: " +str(e))


def main():
    from linkedin_easy_apply.importer import import_text_logs
    from linkedin_easy_apply.store import Store, default_store_path

    start = time.time()
    bot = None
    store = Store(default_store_path())
    import_text_logs(store)
    run_id = os.environ.get("LINKEDIN_RUN_ID") or store.start_run()
    try:
        bot = Linkedin(store=store, run_id=run_id)
        bot.linkJobApply()
        store.finish_run(run_id, "completed")
        return 0
    except Exception as e:  # noqa: BLE001 - top-level crash boundary records run status
        store.finish_run(run_id, "crashed")
        prRed("Error in main: " +str(e))
        traceback.print_exc()
        return 1
    finally:
        prYellow("---Took: " + str(round((time.time() - start)/60)) + " minute(s).")
        if bot is not None:
            bot.driver.quit()


if __name__ == "__main__":
    raise SystemExit(main())
