import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# General bot settings

# browser you want the bot to run ex: ["Firefox"], ["Chrome"] choose one only
# Firefox reuses FIREFOX_PROFILE_PATH so LinkedIn login is kept without a stored password.
browser = ["Firefox"]
# Optional! run browser in headless mode, no browser screen will be shown it will work in background.
headless = False
# Optional! for Firefox enter profile dir to run the bot without logging in your account each time
firefoxProfileRootDir = os.getenv("FIREFOX_PROFILE_PATH", "")
# If you left above field empty enter your Linkedin password and username below
# Linkedin credits
email = os.getenv("LINKEDIN_EMAIL", "")
password = os.getenv("LINKEDIN_PASSWORD", "")

def _default_resume_path() -> str:
    """Use a local *Resume*.pdf in the project root when LINKEDIN_RESUME_PATH is unset."""
    root = os.path.dirname(os.path.abspath(__file__))
    try:
        matches = [
            os.path.join(root, name)
            for name in os.listdir(root)
            if name.lower().endswith(".pdf") and "resume" in name.lower()
        ]
    except OSError:
        return ""
    return matches[0] if matches else ""


resume_path = os.getenv("LINKEDIN_RESUME_PATH", _default_resume_path())

def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "")
    if not str(raw).strip():
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "")
    if not str(raw).strip():
        return default
    try:
        return min(1.0, max(0.0, float(raw)))
    except ValueError:
        return default


# human = slow randomized delays and application caps. fast is only for local tests.
pace = os.getenv("LINKEDIN_PACE", "human").strip().lower() or "human"
max_applications_per_run = _env_int("LINKEDIN_MAX_APPLICATIONS_PER_RUN", 12)
max_applications_per_day = _env_int("LINKEDIN_MAX_APPLICATIONS_PER_DAY", 25)

llm_mode = os.getenv("LINKEDIN_LLM", "auto").strip().lower() or "auto"
ollama_host = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").strip() or "http://127.0.0.1:11434"
ollama_model = os.getenv("OLLAMA_MODEL", "llama3.2").strip() or "llama3.2"
# Local Llama job-fit gate. Skip the listing when fit is below this or decision is skip.
job_fit_threshold = _env_float("LINKEDIN_JOB_FIT_THRESHOLD", 0.55)

# These settings are for running Linkedin job apply bot
LinkedinBotProPasswrod = os.getenv("LINKEDIN_BOT_PRO_PASSWORD", "")
# location you want to search the jobs - ex : ["Poland", "Singapore", "New York City Metropolitan Area", "Monroe County"]
# continent locations:["Europe", "Asia", "Australia", "NorthAmerica", "SouthAmerica", "Africa", "Australia"]
location = ["United States"]
# keywords related with your job search
keywords = [
    "Senior Data Engineer",
    "Data Engineer",
    "Azure Data Engineer",
    "Data Platform Engineer",
    "ETL Developer",
    "SQL Developer",
    "Databricks Engineer",
]
#job experience Level - ex:  ["Internship", "Entry level" , "Associate" , "Mid-Senior level" , "Director" , "Executive"]
experienceLevels = ["Associate", "Mid-Senior level"]
#job posted date - ex: ["Any Time", "Past Month" , "Past Week" , "Past 24 hours"] - select only one
datePosted = ["Past Week"]
#job type - ex:  ["Full-time", "Part-time" , "Contract" , "Temporary", "Volunteer", "Intership", "Other"]
jobType = ["Full-time", "Contract"]
#remote  - ex: ["On-site" , "Remote" , "Hybrid"]
remote = ["Remote", "Hybrid"]
#salary - ex:["$40,000+", "$60,000+", "$80,000+", "$100,000+", "$120,000+", "$140,000+", "$160,000+", "$180,000+", "$200,000+" ] - select only one
salary = ["$120,000+"]
#sort - ex:["Recent"] or ["Relevent"] - select only one
sort = ["Recent"]
#Blacklist companies you dont want to apply - ex: ["Apple","Google"]
blacklist = ["EPAM Anywhere"]
#Blaclist keywords in title - ex:["manager", ".Net"]
blackListTitles = ["intern", "internship", "junior", "entry level", "staffing", "unpaid"]
#Only Apply these companies -  ex: ["Apple","Google"] -  leave empty for all companies 
onlyApply = [""]
#Only Apply titles having these keywords -  ex:["web", "remote"] - leave empty for all companies 
onlyApplyTitles = [
    "data engineer",
    "data platform",
    "etl",
    "azure data",
    "databricks",
    "sql developer",
    "sql engineer",
    "analytics engineer",
    "data warehouse",
]
#Follow companies after sucessfull application True - yes, False - no
followCompanies = False
# your country code for the phone number - ex: fr
country_code = "us"
# Your phone number without identifier - ex: 123456789
phone_number = os.getenv("LINKEDIN_PHONE_NUMBER", "")
# City used when an Easy Apply contact form requires "Location (city)".
application_city = os.getenv("LINKEDIN_APPLICATION_CITY", "")

# Bootstrap skill-year mappings for Easy Apply. These are not model training
# and are not a generic "default years" fallback. Unmatched year questions stay
# empty until approved memory or Ollama can answer them. More specific keys
# must stay above generic ones because the first match wins.
years_experience = {
    "azure data factory": 6,
    "data factory": 6,
    "sql server": 9,
    "power bi": 3,
    "t-sql": 9,
    "tsql": 9,
    "databricks": 4,
    "pyspark": 4,
    "python": 5,
    "ssis": 9,
    "ssrs": 5,
    "spark": 4,
    "azure": 8,
    "sql": 9,
    "terraform": 3,
    "docker": 3,
    "kubernetes": 2,
    "postgresql": 3,
    "postgres": 3,
    "oracle": 3,
    "mongodb": 2,
}

# Bootstrap Yes/No keyword rules. These are hardcoded mappings, not Llama
# training. Generic catch-alls such as experience→Yes are intentionally omitted.
# Every keyword in a tuple must appear in the question. Rules are checked from
# top to bottom.
yes_no_answers = [
    (("sponsorship",), "No"),
    (("active", "security clearance"), "No"),
    (("obtain", "security clearance"), "No"),
    (("non-compete",), "No"),
    (("noncompete",), "No"),
    (("previously employed",), "No"),
    (("previously interviewed",), "No"),
    (("currently employed",), "No"),
    (("related", "employee"), "No"),
    (("authorized", "work", "united states"), "Yes"),
    (("18 years",), "Yes"),
    (("background check",), "Yes"),
    (("drug", "screen"), "Yes"),
    (("driver", "license"), "Yes"),
    (("on-site",), "Yes"),
    (("onsite",), "Yes"),
    (("hybrid",), "Yes"),
    (("remote",), "Yes"),
    (("relocate",), "Yes"),
    (("commute",), "Yes"),
    (("travel",), "Yes"),
    (("weekend",), "Yes"),
    (("evening",), "Yes"),
    (("night", "work"), "Yes"),
    (("overtime",), "Yes"),
    (("contractor",), "Yes"),
    (("temporary", "position"), "Yes"),
    (("compensation", "range"), "Yes"),
    (("salary", "range"), "Yes"),
    (("education", "require"), "Yes"),
    (("bachelor",), "Yes"),
    (("gdpr",), "Yes"),
    (("pci",), "Yes"),
    (("hipaa",), "Yes"),
    (("sox",), "Yes"),
    (("agile",), "Yes"),
    (("scrum",), "Yes"),
    (("on-call",), "Yes"),
    (("on call",), "Yes"),
    (("certification",), "Yes"),
    (("physical", "requirements"), "Yes"),
    (("language", "proficient"), "Yes"),
    (("fluent",), "Yes"),
    (("african",), "Yes"),
    (("w2",), "Yes"),
]


# These settings are for running AngelCO job apply bot
AngelCoBotPassword = ""
# AngelCO credits
AngelCoEmail = ""
AngelCoPassword = ""
# jobTitle ex: ["Frontend Engineer", "Marketing"]
angelCoJobTitle = ["Frontend Engineer"]
# location ex: ["Poland"]
angelCoLocation = ["Poland"]

# These settings are for running GlobalLogic job apply bot
GlobalLogicBotPassword = ""
# AngelCO credits
GlobalLogicEmail = ""
GlobalLogicPassword = ""
# Functions ex: ["Administration", "Business Development", "Business Solutions", "Content Engineering", 	
# Delivery Enablement", Engineering, Finance, IT Infrastructure, Legal, Marketing, People Development,
# Process Management, Product Support, Quality Assurance,Sales, Sales Enablement,Technology, Usability and Design]
GlobalLogicFunctions = ["Engineering"]
# Global logic experience: ["0-1 years", "1-3 years", "3-5 years", "5-10 years", "10-15 years","15+ years"]
GlobalLogicExperience = ["0-1 years", "1-3 years"]
# Global logic location filter: ["Argentina", "Chile", "Crotia", "Germany", "India","Japan", "Poland"
# Romania, Sweden, Switzerland,Ukraine, United States]
GlobalLogicLocation = ["poland"]
# Freelance yes or no
GlobalLogicFreelance = ["no"]
# Remote work yes or no
GlobalLogicRemoteWork = ["yes"]
# Optional! Keyword:["javascript", "react", "angular", ""]
GlobalLogicKeyword = ["react"]
# Global Logic Job apply settinngs
FirstName = os.getenv("APPLICANT_FIRST_NAME", "Tapiwa")
LastName = os.getenv("APPLICANT_LAST_NAME", "Chikwenya")
Email = os.getenv("LINKEDIN_EMAIL", "")
LinkedInProfileURL = os.getenv("LINKEDIN_PROFILE_URL", "https://www.linkedin.com/in/tapiwa-chikwenya")
Phone = os.getenv("LINKEDIN_PHONE_NUMBER", "") #OPTIONAL
Location = os.getenv("LINKEDIN_APPLICATION_CITY", "") #OPTIONAL
HowDidYouHeard = "" #OPTIONAL
ConsiderMeForFutureOffers = True #true = yes, false = no
