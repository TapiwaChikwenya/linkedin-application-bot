import time,math,random,os,traceback
import utils,constants,config

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from utils import prRed,prYellow,prGreen

from selenium.webdriver.firefox.service import Service
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager
from webdriver_manager.firefox import GeckoDriverManager
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select

class Linkedin:
    modalSelector = (
        "dialog[data-testid='dialog'][open], dialog[open], "
        ".jobs-easy-apply-modal, .jobs-easy-apply-content, "
        "[class*='jobs-easy-apply-modal'], [class*='easy-apply-modal'], "
        ".artdeco-modal, [data-test-modal], div[role='dialog']"
    )

    def __init__(self):
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

        self.wait = WebDriverWait(self.driver, 20)
        self.driver.set_window_size(1440, 1000)
        self.login_if_needed(linkedinEmail, config.password)

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
                for url in linkedinJobLinks:
                    file.write(url+ "\n")
            prGreen("Urls are created successfully, now the bot will visit those urls.")
        except:
            prRed("Couldnt generate url, make sure you have /data folder and modified config.py file for your preferances.")

    def linkJobApply(self):
        self.generateUrls()
        countApplied = 0
        countJobs = 0

        urlData = utils.getUrlDataFile()

        for url in urlData:        
            self.driver.get(url)

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

            for page in range(totalPages):
                currentPageJobs = constants.jobsPerPage * page
                pageUrl = url.rstrip() + "&start=" + str(currentPageJobs)
                self.driver.get(pageUrl)
                time.sleep(random.uniform(1, constants.botSpeed))

                offersPerPage = self.driver.find_elements(By.XPATH,'//li[@data-occludable-job-id]')

                offerIds = []
                for offer in offersPerPage:
                    offerId = offer.get_attribute("data-occludable-job-id")
                    offerIds.append(int(offerId.split(":")[-1]))

                for jobID in offerIds:
                    offerPage = 'https://www.linkedin.com/jobs/view/' + str(jobID)
                    self.driver.get(offerPage)
                    time.sleep(random.uniform(1, constants.botSpeed))

                    countJobs += 1

                    jobProperties = self.getJobProperties(countJobs) 
                    
                    button = self.easyApplyButton()

                    if button is not False:
                        button.click()
                        result, applied = self.applyProcess(offerPage, jobID)
                        if applied:
                            countApplied += 1
                        self.displayWriteResults(jobProperties + " | " + result)
                    else:
                        lineToWrite = jobProperties + " | " + "* Easy Apply unavailable or already applied. Job: " +str(offerPage)
                        self.displayWriteResults(lineToWrite)


            prYellow("Category: " + urlWords[0] + "," +urlWords[1]+ " applied: " + str(countApplied) +
                  " jobs out of " + str(countJobs) + ".")
        
        utils.donate(self)

    def getJobProperties(self, count):
        def first_text(selectors):
            for by, selector in selectors:
                for element in self.driver.find_elements(by, selector):
                    if element.text.strip():
                        return " ".join(element.text.split())
            return ""

        jobTitle = first_text([
            (By.CSS_SELECTOR, "h1"),
            (By.CSS_SELECTOR, ".job-details-jobs-unified-top-card__job-title"),
        ])
        jobCompany = first_text([
            (By.CSS_SELECTOR, ".job-details-jobs-unified-top-card__company-name"),
            (By.CSS_SELECTOR, ".jobs-unified-top-card__company-name"),
        ])
        jobLocation = first_text([
            (By.CSS_SELECTOR, ".job-details-jobs-unified-top-card__primary-description-container"),
            (By.CSS_SELECTOR, ".jobs-unified-top-card__bullet"),
        ])
        jobWorkPlace = first_text([(By.CSS_SELECTOR, ".job-details-jobs-unified-top-card__workplace-type")])
        jobPostedDate = first_text([(By.CSS_SELECTOR, "time"), (By.CSS_SELECTOR, ".jobs-unified-top-card__posted-date")])
        jobApplications = first_text([(By.CSS_SELECTOR, ".jobs-unified-top-card__applicant-count")])
        return " | ".join(map(str, [count, jobTitle, jobCompany, jobLocation, jobWorkPlace, jobPostedDate, jobApplications]))

    def easyApplyButton(self):
        try:
            return self.wait.until(EC.element_to_be_clickable((
                By.XPATH,
                "//button[contains(@aria-label, 'Easy Apply') or .//span[normalize-space()='Easy Apply']]"
            )))
        except TimeoutException:
            return False

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
        try:
            dialog = self.wait.until(lambda driver: self.findApplicationContainer())
            for _ in range(10):
                dialog = self.wait.until(lambda driver: self.findApplicationContainer())
                self.fillKnownFields(dialog)
                scroll_areas = dialog.find_elements(By.CSS_SELECTOR, ".artdeco-modal__content, [data-test-modal-content]")
                for scroll_area in scroll_areas or [dialog]:
                    self.driver.execute_script(
                        "arguments[0].scrollTop = arguments[0].scrollHeight;", scroll_area
                    )
                time.sleep(0.25)
                action = self.findApplicationAction(dialog, ("submit",))
                if action:
                    if not config.followCompanies:
                        checked = dialog.find_elements(By.CSS_SELECTOR, "input[id*='follow-company'][type='checkbox']:checked")
                        if checked:
                            self.driver.execute_script("arguments[0].click();", checked[0])
                    self.clickApplicationAction(action)
                    self.wait.until(lambda d: not self.findApplicationContainer()
                                    or d.find_elements(By.XPATH, "//*[contains(., 'Application submitted') or contains(., 'application was sent')]"))
                    return "* 🥳 Just Applied to this job: " + offerPage, True

                action = self.findApplicationAction(dialog, ("review", "next"))
                if not action:
                    return self.applicationFailure(dialog, offerPage, jobID), False
                self.clickApplicationAction(action)
                time.sleep(0.5)
                errors = dialog.find_elements(By.CSS_SELECTOR, "[role='alert'], .artdeco-inline-feedback__message")
                if any(e.is_displayed() and e.text.strip() for e in errors):
                    return self.applicationFailure(dialog, offerPage, jobID), False
            return self.applicationFailure(dialog, offerPage, jobID), False
        except Exception as exc:
            prYellow("Easy Apply error: " + str(exc)[:200])
            traceback.print_exc()
            dialog = self.findApplicationContainer()
            return self.applicationFailure(dialog if dialog else None, offerPage, jobID), False

    def findApplicationContainer(self):
        for element in self.driver.find_elements(By.CSS_SELECTOR, self.modalSelector):
            try:
                if element.is_displayed():
                    return element
            except WebDriverException:
                continue
        # LinkedIn also A/B tests an unlabelled container. Anchor it by its title
        # and application action instead of relying on a generated class name.
        xpath = (
            "//*[self::h1 or self::h2 or self::h3][contains(normalize-space(.), 'Apply to')]"
            "/ancestor::div[.//button[normalize-space(.)='Next' or "
            "contains(@aria-label,'Continue') or contains(@aria-label,'Submit')]][1]"
        )
        for element in self.driver.find_elements(By.XPATH, xpath):
            try:
                if element.is_displayed():
                    return element
            except WebDriverException:
                continue
        return False

    @staticmethod
    def applicationAction(button):
        text = " ".join(filter(None, [button.text, button.get_attribute("aria-label")])).lower()
        if "submit application" in text:
            return "submit"
        if "review" in text:
            return "review"
        if "next" in text or "continue" in text:
            return "next"
        return ""

    def findApplicationAction(self, dialog, wanted):
        """Locate only the requested modal action, avoiding unrelated dynamic buttons."""
        expressions = {
            "submit": "contains(translate(@aria-label,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'submit application') or contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'submit application')",
            "review": "contains(translate(@aria-label,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'review') or contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'review')",
            "next": "contains(translate(@aria-label,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'next') or contains(translate(@aria-label,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'continue') or normalize-space(.)='Next' or contains(normalize-space(.),'Continue')",
        }
        for action_name in wanted:
            xpath = "//button[" + expressions[action_name] + "]"
            try:
                # LinkedIn renders the modal footer as a sibling of the form body,
                # so Review/Next/Submit may be outside the detected content element.
                for button in self.driver.find_elements(By.XPATH, xpath):
                    if button.is_displayed() and button.is_enabled():
                        return button
            except WebDriverException:
                continue
        return None

    def clickApplicationAction(self, button):
        try:
            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
            self.driver.execute_script("arguments[0].click();", button)
        except WebDriverException:
            # Re-raise with the precise operation instead of a generic modal failure.
            raise RuntimeError("LinkedIn replaced the selected application action before it could be clicked")

    def fillKnownFields(self, dialog):
        for select_element in dialog.find_elements(By.CSS_SELECTOR, "select[id*='phoneCountry'], select[name*='phoneCountry']"):
            try:
                country_value = "urn:li:country:" + config.country_code.lower()
                Select(select_element).select_by_value(country_value)
            except (NoSuchElementException, ValueError):
                pass
        if getattr(config, "application_city", ""):
            city_field = self.findFieldByLabel(dialog, ("location", "city"))
            if city_field and not (city_field.get_attribute("value") or "").strip():
                city_field.click()
                city_field.send_keys(Keys.CONTROL, "a")
                city_field.send_keys(config.application_city)
                try:
                    suggestion = WebDriverWait(self.driver, 5).until(EC.element_to_be_clickable((
                        By.CSS_SELECTOR,
                        "[role='listbox'] [role='option'], .basic-typeahead__selectable"
                    )))
                    suggestion.click()
                except TimeoutException:
                    # Keyboard selection also triggers LinkedIn's autocomplete state.
                    city_field.send_keys(Keys.ARROW_DOWN)
                    city_field.send_keys(Keys.ENTER)
                city_field.send_keys(Keys.TAB)

        if config.phone_number:
            for field in dialog.find_elements(By.CSS_SELECTOR, "input[type='tel'], input[id*='phone']"):
                if not field.get_attribute("value"):
                    field.send_keys(config.phone_number)

        experience_answers = getattr(config, "years_experience", {})
        if experience_answers:
            for field in dialog.find_elements(By.CSS_SELECTOR, "input:not([type='hidden'])"):
                try:
                    question = self.fieldMetadata(field)
                    if "year" not in question or "experience" not in question:
                        continue
                    if (field.get_attribute("value") or "").strip():
                        continue
                    answer = experience_answers.get("default", 0)
                    for skill, years in experience_answers.items():
                        if skill != "default" and skill.lower() in question:
                            answer = years
                            break
                    field.click()
                    field.send_keys(Keys.CONTROL, "a")
                    field.send_keys(str(answer))
                    field.send_keys(Keys.TAB)
                except WebDriverException:
                    continue

        for group in dialog.find_elements(By.CSS_SELECTOR, "fieldset[role='radiogroup']"):
            try:
                if group.find_elements(By.CSS_SELECTOR, "[role='radio'][aria-checked='true'], input[type='radio']:checked"):
                    continue
                question = self.driver.execute_script("""
                    const group = arguments[0];
                    const labelledBy = group.getAttribute('aria-labelledby');
                    const labelled = labelledBy ? document.getElementById(labelledBy) : null;
                    const previous = group.previousElementSibling;
                    return (labelled?.innerText || previous?.innerText || '').trim().toLowerCase();
                """, group)
                answer = self.yesNoAnswerForQuestion(question)
                if not answer:
                    continue
                for option in group.find_elements(By.CSS_SELECTOR, "[role='radio']"):
                    if option.text.strip().lower() == answer.lower():
                        self.driver.execute_script("arguments[0].click();", option)
                        break
            except WebDriverException:
                continue

    @staticmethod
    def yesNoAnswerForQuestion(question):
        normalized = " ".join(question.lower().replace("‑", "-").split())
        for keywords, answer in getattr(config, "yes_no_answers", []):
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
        for field in dialog.find_elements(By.CSS_SELECTOR, "input:not([type='hidden']), textarea"):
            try:
                metadata = self.fieldMetadata(field)
                if any(word in metadata for word in words) and field.is_displayed():
                    return field
            except WebDriverException:
                continue
        return None

    def applicationFailure(self, dialog, offerPage, jobID):
        os.makedirs("data", exist_ok=True)
        self.driver.save_screenshot(os.path.join("data", "easy_apply_failure_" + str(jobID) + ".png"))
        with open(os.path.join("data", "easy_apply_page_" + str(jobID) + ".html"), "w", encoding="utf-8") as file:
            file.write(self.driver.page_source)
        if dialog:
            try:
                modal_html = self.driver.execute_script("return arguments[0].outerHTML;", dialog)
                with open(os.path.join("data", "easy_apply_failure_" + str(jobID) + ".html"), "w", encoding="utf-8") as file:
                    file.write(modal_html)
            except WebDriverException:
                pass
        details = []
        if dialog:
            for element in dialog.find_elements(By.CSS_SELECTOR, "[role='alert'], .artdeco-inline-feedback__message, label"):
                text = " ".join(element.text.split())
                if text and text not in details:
                    details.append(text)
        reason = "; ".join(details[:5]) or "extra information or a required answer is needed"
        return "* 🥵 Couldn't apply: " + reason + ". Link: " + offerPage

    def displayWriteResults(self,lineToWrite: str):
        try:
            print(lineToWrite)
            utils.writeResults(lineToWrite)
        except Exception as e:
            prRed("Error in DisplayWriteResults: " +str(e))


def main():
    start = time.time()
    bot = None
    try:
        bot = Linkedin()
        bot.linkJobApply()
        return 0
    except Exception as e:
        prRed("Error in main: " +str(e))
        traceback.print_exc()
        return 1
    finally:
        prYellow("---Took: " + str(round((time.time() - start)/60)) + " minute(s).")
        if bot is not None:
            bot.driver.quit()


if __name__ == "__main__":
    raise SystemExit(main())
