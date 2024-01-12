import os
import pandas as pd
from flask import Flask, render_template, request, flash, redirect, url_for, send_file
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['SECRET_KEY'] = '6bb2d52afe4d90db2994dec64acfa174'
app.config['UPLOAD_FOLDER'] = 'uploads'

ALLOWED_EXTENSIONS = {'csv'}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/upload_collection', methods=['GET', 'POST'])
def upload_collection():
    if request.method == 'POST':
        file = request.files['collection_file']
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(file_path)
            flash('File successfully uploaded')
            return redirect(url_for('process_collection', filename=filename))
    return render_template('upload_collection.html')


@app.route('/process_collection/<filename>')
def process_collection(filename):
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    collection_df = pd.read_csv(file_path)

    # Normalize the name to lowercase for comparison
    collection_df['Name'] = collection_df['Name'].str.lower()

    # Standardize 'Quantity' column
    if 'Count' in collection_df.columns:
        collection_df.rename(columns={'Count': 'Quantity'}, inplace=True)
    elif 'Quantity' not in collection_df.columns:
        flash('The CSV file does not have a "Quantity" column.')
        return redirect(url_for('index'))

    collection_df['Binder Name'] = collection_df.get('Binder Name', '')

    # Aggregate quantities for cards with the same name
    aggregation_functions = {'Quantity': 'sum', 'Binder Name': lambda x: ', '.join(set(x))}
    collection_df = collection_df.groupby('Name').agg(aggregation_functions).reset_index()

    flash('Collection processed')
    return render_template('processed_collection.html', tables=[collection_df.to_html(classes='data', escape=False)],
                           titles=['Collection'])


@app.route('/compare_decklist', methods=['GET', 'POST'])
def compare_decklist():
    if request.method == 'POST':
        # Assume 'decklist_file' contains a text list of cards, one per line
        decklist_text = request.form['decklist']
        decklist_lines = decklist_text.strip().split('\n')
        decklist_data = {'Name': [], 'Quantity': []}
        for line in decklist_lines:
            parts = line.split(maxsplit=1)
            if len(parts) == 2:
                quantity, name = parts
                try:
                    decklist_data['Quantity'].append(int(quantity))
                    decklist_data['Name'].append(name.strip().lower())
                except ValueError:
                    flash("There was an error with the decklist format.")
                    return redirect(url_for('compare_decklist'))

        decklist_df = pd.DataFrame(decklist_data)

        # Process the collection CSV
        collection_file = request.files['collection_file']
        collection_df = pd.read_csv(collection_file)
        collection_df['Name'] = collection_df['Name'].str.lower()

        collection_text = '\n'.join(f"{row['Quantity']} {row['Name'].title()}" for _, row in collection_df.iterrows())


        # Perform comparison and determine missing and present cards
        comparison_df = decklist_df.merge(collection_df, on='Name', how='left')
        comparison_df['Quantity_y'] = comparison_df['Quantity_y'].fillna(0).astype(int)
        comparison_df['In Collection'] = comparison_df['Quantity_y'] >= comparison_df['Quantity_x']
        comparison_df['Missing'] = comparison_df['Quantity_x'] - comparison_df['Quantity_y']

        # Extract rows for cards that are present in the collection
        present_cards_df = comparison_df[comparison_df['In Collection']].copy()

        # Prepare binder data for detailed quantities
        if 'Binder Name' in collection_df.columns:
            binder_agg = collection_df.groupby(['Name', 'Binder Name']).agg({'Quantity': 'sum'}).reset_index()
            binder_info = binder_agg.groupby('Name').apply(
                lambda x: ', '.join([f"{row['Binder Name']} - {row['Quantity']}x" for _, row in x.iterrows()])
            ).to_dict()
        else:
            binder_info = {}

        # Merge decklist with collection and calculate owned and missing quantities
        merged_df = pd.merge(decklist_df, collection_df.groupby('Name').agg({'Quantity': 'sum'}).reset_index(),
                             on='Name', how='left', indicator=True)
        merged_df['Quantity_y'] = merged_df['Quantity_y'].fillna(0).astype(int)
        merged_df['Owned'] = merged_df.apply(lambda x: min(x['Quantity_x'], x['Quantity_y']), axis=1)
        merged_df['Missing'] = merged_df.apply(lambda x: max(0, x['Quantity_x'] - x['Owned']), axis=1)

        # Prepare display data for owned and missing cards
        cards_in_both_display = merged_df[merged_df['Owned'] > 0].apply(
            lambda x: f"{x['Owned']} {x['Name'].title()} [{binder_info.get(x['Name'], '')}]",
            axis=1
        )
        missing_cards_display = merged_df[merged_df['Missing'] > 0].apply(
            lambda x: f"{x['Missing']} {x['Name'].title()}",
            axis=1
        )

        # Generate text for textareas
        owned_text = '\n'.join(cards_in_both_display)
        missing_text = '\n'.join(missing_cards_display)

        # Aggregate quantities and binder names, and calculate the total count
        present_cards_agg = present_cards_df.groupby('Name').agg({
            'Quantity_x': 'sum',  # Sum the quantities across all binders
            'Binder Name': lambda x: ', '.join(sorted(set(x.dropna())))  # Concatenate unique binder names
        }).reset_index()

        # Append the total count to the binder info
        present_cards_agg['Display'] = present_cards_agg.apply(
            lambda x: f"{x['Quantity_x']} {x['Name'].title()} [{x['Binder Name']} (Total {x['Quantity_x']}x)]",
            axis=1
        )

        # Pass text to the template
        return render_template(
            'compare_decklist.html',
            collection_text=collection_text,
            decklist_text=decklist_text,
            missing_text=missing_text,
            owned_text=owned_text
        )
    else:
        # GET request logic
        return render_template('compare_decklist.html')


@app.route('/convert_csv', methods=['GET', 'POST'])
def convert_csv():
    if request.method == 'POST':
        file = request.files['file']
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(file_path)

            # Read the CSV and convert it
            df = pd.read_csv(file_path)
            new_df = pd.DataFrame({
                "Count": df["Quantity"],
                "Name": df["Name"],
                "Edition": df["Set name"],
                "Condition": df["Condition"].str.replace('_', ' ').str.capitalize(),
                "Language": df["Language"],
                "Foil": df["Foil"].apply(lambda x: 'foil' if 'foil' in x.lower() else ''),
                "Collector Number": df["Collector number"],
                "Alter": df["Altered"],
                "Proxy": df["Misprint"],
                "Purchase Price": df["Purchase price"]
            })

            converted_file_path = os.path.join(app.config['UPLOAD_FOLDER'], 'converted_' + filename)
            new_df.to_csv(converted_file_path, index=False)
            return send_file(converted_file_path, as_attachment=True)
    return render_template('convert_csv.html')

@app.route('/compare_csvs', methods=['GET', 'POST'])
def compare_csvs():
    if request.method == 'POST':
        file1 = request.files['file1']
        file2 = request.files['file2']
        if file1 and file2 and allowed_file(file1.filename) and allowed_file(file2.filename):
            filename1 = secure_filename(file1.filename)
            file_path1 = os.path.join(app.config['UPLOAD_FOLDER'], filename1)
            file1.save(file_path1)

            filename2 = secure_filename(file2.filename)
            file_path2 = os.path.join(app.config['UPLOAD_FOLDER'], filename2)
            file2.save(file_path2)

            df1 = pd.read_csv(file_path1)
            df2 = pd.read_csv(file_path2)

            df_diff = pd.concat([df1, df2]).drop_duplicates(keep=False)
            diff_file_path = os.path.join(app.config['UPLOAD_FOLDER'], 'diff_' + filename1)
            df_diff.to_csv(diff_file_path, index=False)

            return send_file(diff_file_path, as_attachment=True)
    return render_template('compare_csvs.html')

if __name__ == '__main__':
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)