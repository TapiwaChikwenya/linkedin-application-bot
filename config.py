import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# General bot settings

# browser you want the bot to run ex: ["Firefox"], ["Chrome"] choose one only
browser = ["Firefox"]
# Optional! run browser in headless mode, no browser screen will be shown it will work in background.
headless = False
# Optional! for Firefox enter profile dir to run the bot without logging in your account each time
firefoxProfileRootDir = os.getenv("FIREFOX_PROFILE_PATH", "")
# If you left above field empty enter your Linkedin password and username below
# Linkedin credits
email = os.getenv("LINKEDIN_EMAIL", "")
password = os.getenv("LINKEDIN_PASSWORD", "")

# These settings are for running Linkedin job apply bot
LinkedinBotProPasswrod = os.getenv("LINKEDIN_BOT_PRO_PASSWORD", "")
# location you want to search the jobs - ex : ["Poland", "Singapore", "New York City Metropolitan Area", "Monroe County"]
# continent locations:["Europe", "Asia", "Australia", "NorthAmerica", "SouthAmerica", "Africa", "Australia"]
location = ["NorthAmerica"]
# keywords related with your job search
keywords = ["Data Engineering", "SQL Developer", "Data Analyst", "Business Interlligence", "Power BI developer","SQL", "python", "programming"]
# keywords = ["programming"]
#job experience Level - ex:  ["Internship", "Entry level" , "Associate" , "Mid-Senior level" , "Director" , "Executive"]
experienceLevels = ["Internship", "Entry level" , "Associate" , "Mid-Senior level" , "Director" , "Executive"]
#job posted date - ex: ["Any Time", "Past Month" , "Past Week" , "Past 24 hours"] - select only one
datePosted = ["Past Week" , "Past 24 hours"]
# datePosted = ["Past 24 hours"]
#job type - ex:  ["Full-time", "Part-time" , "Contract" , "Temporary", "Volunteer", "Intership", "Other"]
jobType = ["Full-time", "Part-time" , "Contract"]
#remote  - ex: ["On-site" , "Remote" , "Hybrid"]

remote = ["On-site" , "Remote" , "Hybrid"]
#salary - ex:["$40,000+", "$60,000+", "$80,000+", "$100,000+", "$120,000+", "$140,000+", "$160,000+", "$180,000+", "$200,000+" ] - select only one
salary = [ "$80,000+"]
#sort - ex:["Recent"] or ["Relevent"] - select only one
sort = ["Recent"]
#Blacklist companies you dont want to apply - ex: ["Apple","Google"]
blacklist = ["EPAM Anywhere"]
#Blaclist keywords in title - ex:["manager", ".Net"]
blackListTitles = [""]
#Only Apply these companies -  ex: ["Apple","Google"] -  leave empty for all companies 
onlyApply = [""]
#Only Apply titles having these keywords -  ex:["web", "remote"] - leave empty for all companies 
onlyApplyTitles = [""] 
#Follow companies after sucessfull application True - yes, False - no
followCompanies = False
# your country code for the phone number - ex: fr
country_code = "us"
# Your phone number without identifier - ex: 123456789
phone_number = os.getenv("LINKEDIN_PHONE_NUMBER", "")
# City used when an Easy Apply contact form requires "Location (city)".
application_city = os.getenv("LINKEDIN_APPLICATION_CITY", "")

# Truthful years of experience used for Easy Apply screening questions.
years_experience = {
    "python": 5,
    "sql": 8,
    "power bi": 5,
    "default": 5,
}

# Truthful answers to common Yes/No screening questions. Every keyword in a
# tuple must appear in the question. Rules are checked from top to bottom.
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
    (("certification",), "Yes"),
    (("years", "experience"), "Yes"),
    (("physical", "requirements"), "Yes"),
    (("language", "proficient"), "Yes"),
    (("fluent",), "Yes"),
    (("experience",), "Yes"),
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
FirstName = "O"
LastName = "D"
Email = "amin@boulouma.com"
LinkedInProfileURL = "www.google.com"
Phone = "" #OPTIONAL
Location = "" #OPTIONAL
HowDidYouHeard = "" #OPTIONAL
ConsiderMeForFutureOffers = True #true = yes, false = no
