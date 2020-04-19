import sys
import argparse
from glob import glob
from pprint import pprint
from typing import List
import pytesseract
import cv2
from slugify import slugify
import meilisearch
from meilisearch import Client
from meilisearch.index import Index


class CuillereDArgent:
    def __init__(self, url: str, uid: str, name: str, debug: bool = False):
        # Declare the MeiliSearch information
        self.meilisearch_url: str = url
        self.meilisearch_uid: str|int = uid
        self.meilisearch_name: str = name

        # Declare the MeiliSearch client
        self.client: Client = None

        # Declare the MeiliSearch index
        self.index: Index = None

        # The book is in French, so define Tesseract
        # as French
        self.tesseract_lang = "fra"

        # Set the PSM's we need to read images
        self.tesseract_config = ("--psm 4", "--psm 6")

        # Search the images path
        self.images_path: List[str] = glob("images/**/*.jpg")
        self.images_path = sorted(self.images_path)

        # Set the empty recipes list
        self.recipes: List[List[str]] = []

        # Store the last recipe category found
        self.category = ""

        # Set the debug mode
        self.debug: bool = debug

        # Connect to MeiliSearch
        self.connect_to_meilisearch()

    def connect_to_meilisearch(self):
        """
        Create a connection to MeiliSearch and
        get or create the index.
        """
        # Init instance of MeiliSearch
        self.client = meilisearch.Client(self.meilisearch_url)

        # Try to connect to MeiliSearch
        try:
            self.client.health()
        except:
            sys.exit(f"\033[31mNo instance of MeiliSearch is running on {self.meilisearch_url} ...")

        # Search in all indexes if the index exists or not
        exist = next((index for index in self.client.get_indexes()\
            if index["uid"] == self.meilisearch_uid), None)

        if exist is not None:
            self.index = self.client.get_index(self.meilisearch_uid)
        else:
            self.index = self.client.create_index(self.meilisearch_uid, name=self.meilisearch_name)

    def walk_images(self):
        """
        Walk on all images and read them one by one.
        """
        # Count the number of images
        total_images = len(self.images_path)

        # Quit here if no image found
        if total_images == 0:
            sys.exit("Nothing to index, it was a pleasure :)")

        # Debug info
        if self.debug:
            pprint(self.images_path)

        # Loop over all images and read them
        for image_path_index, image_path in enumerate(self.images_path, start=1):
            # Print to console advancement
            print(f"Reading image {image_path_index} of {total_images}...", end="\r")

            # Read the image
            self.read(image_path)

        print("Finished to read all images.")

    def read(self, path: str):
        """
        Read the content of a page and organize
        the returned list.

        Params:
            path: Path of the image to read.
        """
        # Open the image with OpenCV
        img_cv = cv2.imread(path)

        # Convert colors from BGR to GRAY
        img_rgb = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)

        # Read the current image to find categories
        categories = pytesseract.image_to_string(img_rgb, \
            lang=self.tesseract_lang, config=self.tesseract_config[0])

        # Read the current image to find recipes with the page
        unordered_recipes = pytesseract.image_to_string(img_rgb, \
            lang=self.tesseract_lang, config=self.tesseract_config[1]) \
            .split("\n")

        # Remove empty elements in the recipes list
        unordered_recipes = list(filter(None, unordered_recipes))

        # Remove non alpha numeric elements
        unordered_recipes = list(filter(self.filter_recipes, unordered_recipes))

        # Clean elements
        unordered_recipes = list(map(self.clean_recipe, unordered_recipes))

        # Merge recipe on multiple lines into one line
        recipes: List[str] = []

        for recipe in unordered_recipes:
            # Recipe in uppercase and starting with a number
            # can be append to the list directly
            if recipe.isupper() or recipe.split(" ")[0].isnumeric():
                recipes.append(recipe)
            else:
                # If not, which means it's a recipe next line name
                recipes[-1] = f"{recipes[-1]} {recipe}"

        # Remove categories and keep only subcategories
        # Header = SAUCES, MARINADES ET BEURRES AROMATISÉS
        # Category = SAUCES CHAUDES
        found_breakline: bool = False
        category_walker: List[str] = []
        headers: List[str] = []

        for category in categories.split("\n"):
            # In case the category is upper means
            # it's a header or a category
            if category.isupper():
                # If a breakline has been found before,
                # and a new category has been found too,
                # this means that before it was a header,
                # and now we found a category
                if found_breakline is True:
                    # Reset vars to their initial states
                    # as header is unwanted
                    found_breakline = False
                    headers = headers + category_walker
                    category_walker = []

                # Add the category to the list to inspect
                category_walker.append(category)

            # If a breakline is found,
            # flag it as true
            elif category == "":
                found_breakline = True

            # In case it's not uppercase,
            # this mean it's a recipe
            elif category.isupper() is False:
                # Reset vars as we may need
                # to search category again
                found_breakline = False
                category_walker = []

        # Loop over headers and remove them
        # from the recipes list
        for header in headers:
            recipes = [el for el in recipes if slugify(el) != slugify(header)]

        # Store current category.
        # This is required because some categories
        # can be on multiple lines
        current_category = ""

        # Loop over recipes and split the line to have
        # the page number and the name of the recipe
        for recipe in recipes:
            # Check if the recipe is a category
            # by checking if it is uppercase
            if recipe.isupper():
                # If the category is found in categories,
                # set it as the latest category found,
                # like this, next recipies can use it as reference
                current_category = f"{current_category} {recipe}"
                continue

            # Set the last category found
            if len(current_category) > 0:
                self.category = current_category.strip().capitalize()

                # Reset category name
                current_category = ""

            # Split the recipe by the first space character
            recipe_parts = recipe.split(" ", 1)

            # To comment
            if recipe_parts[0].isnumeric() is False:
                continue

            # Add the category as third elements
            recipe_parts.append(self.category)

            # Remove leading and trailing whitespaces
            recipe_parts = list(map(lambda el: el.strip(), recipe_parts))

            # Add the recipe to the list to index
            self.recipes.append(recipe_parts)

    def index_recipes(self):
        """
        Index all recipes to MeiliSearch.
        """
        # Store the total of recipes
        total_recipes = len(self.recipes)

        # Stop here if no recipes found
        if total_recipes == 0:
            sys.exit("No recipe found...")
        else:
            # Remove all documents
            self.index.delete_all_documents()

        # Index to incremente for indexation purpose
        recipe_index = 1

        # Loop over all recipes and index them
        for index, recipe in enumerate(self.recipes, start=1):
            # Print to console advancement
            print(f"Indexing {index} of {total_recipes}", end="\r")

            # Send document to MeiliSearch
            self.index.add_documents([{
                "recipe_id": recipe_index,
                "category": recipe[2],
                "name": recipe[1],
                "page": int(recipe[0])
            }])

            # Incremente the index
            recipe_index = recipe_index + 1

        # Print end message
        print("Indexation finished! Enjoy to cook a lot of good recipes :)")

    def filter_recipes(self, recipe: str) -> bool:
        """
        Check if the recipe is alphanumeric with some
        allowed characters.

        Params:
            recipe: Recipe to filter on.
        """
        for char in [" ", ",", "(", ")", "-", "|"]:
            recipe = recipe.replace(char, "")

        return recipe.isalnum()

    def clean_recipe(self, recipe: str) -> str:
        """
        Remove unwanted characters in recipes
        or correct words no well read.

        Params:
            recipe: Recipe string to clean.
        """
        to_correct = {
            "|": "",
            "pates": "pâtes",
            "PATES": "PÂTES"
        }

        for old, new in to_correct.items():
            recipe = recipe.replace(old, new)

        return recipe


def main():
    # Define the CLI arguments
    parser = argparse.ArgumentParser(description="La Cuillère D'Argent MeiliSearch Indexer.")

    parser.add_argument("--url", default="http://127.0.0.1:7700",\
        help="the url to the MeiliSearch API")
    parser.add_argument("--index-uid", default="cuillere-argent",\
        help="id of the index (default \"cuillere-argent\")")
    parser.add_argument("--index-name", default="La Cuillère d'Argent",\
        help="name of the index (default \"La Cuillère d'Argent\")")
    parser.add_argument("--debug", action="store_true", help="display some debug information")

    # Parse the cli arguments
    args = parser.parse_args()

    # Create an instance of the parser
    parser = CuillereDArgent(url=args.url, uid=args.index_uid,\
        name=args.index_name, debug=args.debug)

    # Find all images
    parser.walk_images()

    # Index recipes to MeiliSearch
    parser.index_recipes()


if __name__ == "__main__":
    main()
